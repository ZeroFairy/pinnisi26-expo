#!/usr/bin/env python3
"""
Two-Player Tug-of-War Game — Raspberry Pi 5
=============================================

Hardware (BCM pin numbers) — same wiring as reaction_game.py:
  Button 1 (Player LEFT)  -> GPIO17 -> GND
  Button 2 (Player RIGHT) -> GPIO27 -> GND
  LED 1                   -> GPIO5  (+resistor) -> GND
  LED 2                   -> GPIO6  (+resistor) -> GND
  LED 3                   -> GPIO13 (+resistor) -> GND
  Buzzer (active buzzer)  -> GPIO18 -> GND
  Monitor                 -> HDMI (pygame fullscreen)

Flow:
  1. WAIT_HOLD : both players hold their button together to arm the round.
  2. HOLDING   : hold for 3 seconds, one LED lights per second.
  3. GO        : buzzer beeps, screen flashes "PULL!" and the rope marker
                 starts centered on the progress bar.
  4. TUG       : each button TAP (press edge, not hold) pulls the rope
                 marker toward your side. The marker also slowly drifts
                 back to center every frame, so mashing continuously is
                 required to keep winning — a single tap won't hold it.
                 LEDs mirror how close the marker is to your edge (all 3
                 lit = you're one tap-streak away from winning).
  5. RESULT    : whoever drags the marker fully to their edge wins.
                 Split-screen flash on the monitor + LED blink + buzzer
                 fanfare, then auto-resets to WAIT_HOLD.

Install deps:
  pip install gpiozero pygame --break-system-packages

Run:
  python3 tug_of_war.py
  (add --sim to run without real GPIO hardware, using keyboard keys)
  Sim controls: mash "A" for LEFT, mash "L" for RIGHT (tap repeatedly,
  key-repeat is disabled so each physical keypress = one tap, just like
  a real button)
"""

import sys
import time
import argparse

import pygame

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BTN_LEFT_PIN = 17
BTN_RIGHT_PIN = 27
# Currently only 2 LEDs wired up. LED 3 is commented out for now —
# when you add the 3rd LED, just uncomment its pin below and it will
# automatically be picked up everywhere (countdown, progress, blink),
# since all LED logic scales off len(LED_PINS).
LED_PINS = [
    5,
    6,
    # 13,  # LED 3 -- uncomment once wired up
]
BUZZER_PIN = 18

HOLD_SECONDS = 3.0
RESULT_DISPLAY_SECONDS = 4.0

# Tug-of-war tuning
PULL_PER_TAP = 4.0        # how much one tap moves the marker (in %, 0-100 scale)
DECAY_PER_SECOND = 6.0    # how fast the marker drifts back to center each second
MARKER_START = 50.0       # center of the bar
MARKER_MIN = 0.0          # LEFT wins here
MARKER_MAX = 100.0        # RIGHT wins here

SCREEN_SIZE = (1024, 600)
FPS = 60

COLOR_BG = (15, 15, 20)
COLOR_LEFT = (40, 120, 220)
COLOR_RIGHT = (220, 60, 60)
COLOR_TEXT = (240, 240, 240)
COLOR_ACCENT = (250, 210, 60)
COLOR_BAR_BG = (40, 40, 46)
COLOR_MARKER = (255, 255, 255)

# ---------------------------------------------------------------------------
# Hardware layer — real GPIO or keyboard simulation
# ---------------------------------------------------------------------------
class Hardware:
    def __init__(self, simulate=False):
        self.simulate = simulate
        self._sim_left_taps = 0
        self._sim_right_taps = 0
        self.num_leds = len(LED_PINS)  # everything below scales off this

        if not simulate:
            from gpiozero import Button, LED, Buzzer
            self.btn_left = Button(BTN_LEFT_PIN, pull_up=True, bounce_time=0.02)
            self.btn_right = Button(BTN_RIGHT_PIN, pull_up=True, bounce_time=0.02)
            self.leds = [LED(pin) for pin in LED_PINS]
            self.buzzer = Buzzer(BUZZER_PIN)
            self._left_was_pressed = False
            self._right_was_pressed = False
        else:
            self.btn_left = None
            self.btn_right = None
            self.leds = None
            self.buzzer = None

    # --- raw held-state (used for the "hold both to start" phase) ---
    def left_held(self):
        if self.simulate:
            return self._sim_left_taps > 0  # not used for holding in sim, see key handling
        return self.btn_left.is_pressed

    def right_held(self):
        if self.simulate:
            return self._sim_right_taps > 0
        return self.btn_right.is_pressed

    # --- tap edge detection: returns number of NEW presses since last call ---
    def poll_taps(self):
        """Returns (left_taps, right_taps) counted since the last call."""
        if self.simulate:
            lt, rt = self._sim_left_taps, self._sim_right_taps
            self._sim_left_taps = 0
            self._sim_right_taps = 0
            return lt, rt

        left_now = self.btn_left.is_pressed
        right_now = self.btn_right.is_pressed
        left_tap = 1 if (left_now and not self._left_was_pressed) else 0
        right_tap = 1 if (right_now and not self._right_was_pressed) else 0
        self._left_was_pressed = left_now
        self._right_was_pressed = right_now
        return left_tap, right_tap

    def register_sim_tap(self, key):
        if key in (pygame.K_LEFT, pygame.K_a):
            self._sim_left_taps += 1
        elif key in (pygame.K_RIGHT, pygame.K_l):
            self._sim_right_taps += 1

    # --- LEDs ---
    def leds_set(self, count_on):
        if self.simulate:
            return
        for i, led in enumerate(self.leds):
            led.value = 1 if i < count_on else 0

    def leds_all(self, on):
        self.leds_set(self.num_leds if on else 0)

    def leds_blink(self, times=3, interval=0.12):
        if self.simulate:
            return
        for _ in range(times):
            for led in self.leds:
                led.on()
            time.sleep(interval)
            for led in self.leds:
                led.off()
            time.sleep(interval)

    # --- buzzer ---
    def beep(self, duration=0.1):
        if self.simulate:
            return
        self.buzzer.on()
        time.sleep(duration)
        self.buzzer.off()

    def tick(self):
        if self.simulate:
            return
        self.buzzer.on()
        time.sleep(0.02)
        self.buzzer.off()

    def win_fanfare(self):
        if self.simulate:
            return
        for dur in (0.08, 0.08, 0.2):
            self.buzzer.on()
            time.sleep(dur)
            self.buzzer.off()
            time.sleep(0.05)

    def cleanup(self):
        if not self.simulate:
            for led in self.leds:
                led.off()
            self.buzzer.off()


# ---------------------------------------------------------------------------
# Game states
# ---------------------------------------------------------------------------
WAIT_HOLD = "WAIT_HOLD"
HOLDING = "HOLDING"
GO = "GO"
TUG = "TUG"
RESULT = "RESULT"


class Game:
    def __init__(self, hw: Hardware, screen, simulate):
        self.hw = hw
        self.screen = screen
        self.simulate = simulate
        self.font_big = pygame.font.SysFont("Arial", 90, bold=True)
        self.font_mid = pygame.font.SysFont("Arial", 46, bold=True)
        self.font_small = pygame.font.SysFont("Arial", 26)
        self.reset_to_wait()

    def reset_to_wait(self):
        self.state = WAIT_HOLD
        self.hold_start_time = None
        self.leds_lit = 0
        self.marker = MARKER_START
        self.winner = None
        self.result_start_time = None
        self.last_frame_time = time.time()
        self.hw.leds_all(False)
        # drain any queued sim taps so they don't leak into next round
        self.hw.poll_taps()

    # For WAIT_HOLD in sim mode we need actual "held" state, so track keys down
    def set_sim_held(self, left, right):
        self._sim_left_held = left
        self._sim_right_held = right

    def _held_left(self):
        if self.simulate:
            return getattr(self, "_sim_left_held", False)
        return self.hw.left_held()

    def _held_right(self):
        if self.simulate:
            return getattr(self, "_sim_right_held", False)
        return self.hw.right_held()

    # ---- state handlers -------------------------------------------------
    def update(self):
        now = time.time()
        dt = now - self.last_frame_time
        self.last_frame_time = now

        if self.state == WAIT_HOLD:
            self.hw.poll_taps()  # discard stray taps
            if self._held_left() and self._held_right():
                self.state = HOLDING
                self.hold_start_time = now
                self.leds_lit = 0
                self.hw.leds_set(0)

        elif self.state == HOLDING:
            if not (self._held_left() and self._held_right()):
                self.reset_to_wait()
                return
            elapsed = now - self.hold_start_time
            n = self.hw.num_leds
            # spread the LED-per-second lighting evenly across HOLD_SECONDS,
            # regardless of how many LEDs are currently wired up
            step = HOLD_SECONDS / n
            target_lit = min(n, int(elapsed // step) + 1) if elapsed < HOLD_SECONDS else n
            if target_lit != self.leds_lit:
                self.leds_lit = target_lit
                self.hw.leds_set(self.leds_lit)
            if elapsed >= HOLD_SECONDS:
                self.state = GO
                self.hw.leds_all(True)
                self.hw.beep(0.3)
                self.go_shown_at = now
                self.marker = MARKER_START

        elif self.state == GO:
            self.hw.poll_taps()  # discard taps during the splash
            if now - self.go_shown_at >= 0.6:
                self.state = TUG
                self.marker = MARKER_START

        elif self.state == TUG:
            left_taps, right_taps = self.hw.poll_taps()

            # each tap pulls the marker toward that player's side
            self.marker -= left_taps * PULL_PER_TAP
            self.marker += right_taps * PULL_PER_TAP

            # constant decay back toward center -> must keep tapping
            if self.marker > MARKER_START:
                self.marker = max(MARKER_START, self.marker - DECAY_PER_SECOND * dt)
            elif self.marker < MARKER_START:
                self.marker = min(MARKER_START, self.marker + DECAY_PER_SECOND * dt)

            self.marker = max(MARKER_MIN, min(MARKER_MAX, self.marker))

            # LEDs show how close the LEADING side is to winning (0..num_leds lit)
            n = self.hw.num_leds
            distance_from_center = abs(self.marker - MARKER_START)
            max_distance = MARKER_START - MARKER_MIN  # symmetric
            progress_ratio = distance_from_center / max_distance if max_distance else 0
            lit = min(n, int(progress_ratio * n))
            if lit != self.leds_lit:
                self.leds_lit = lit
                self.hw.leds_set(lit)
                if lit > 0:
                    self.hw.tick()

            if self.marker <= MARKER_MIN:
                self.winner = "LEFT"
                self._enter_result()
            elif self.marker >= MARKER_MAX:
                self.winner = "RIGHT"
                self._enter_result()

        elif self.state == RESULT:
            self.hw.poll_taps()  # discard taps during result screen
            if now - self.result_start_time >= RESULT_DISPLAY_SECONDS:
                self.reset_to_wait()

    def _enter_result(self):
        self.state = RESULT
        self.result_start_time = time.time()
        self.hw.leds_blink(times=self.hw.num_leds, interval=0.12)
        self.hw.leds_all(True)
        self.hw.win_fanfare()

    # ---- rendering --------------------------------------------------------
    def draw(self):
        w, h = self.screen.get_size()
        self.screen.fill(COLOR_BG)

        if self.state == WAIT_HOLD:
            self._center_text("HOLD BOTH BUTTONS TO START", self.font_mid, COLOR_TEXT, h // 2 - 40)
            self._center_text("Player Left & Player Right", self.font_small, COLOR_ACCENT, h // 2 + 30)

        elif self.state == HOLDING:
            self._center_text("HOLD...", self.font_mid, COLOR_TEXT, h // 2 - 80)
            n = self.hw.num_leds
            dots = "● " * self.leds_lit + "○ " * (n - self.leds_lit)
            self._center_text(dots, self.font_big, COLOR_ACCENT, h // 2 + 10)

        elif self.state == GO:
            self._center_text("PULL!!", self.font_big, COLOR_ACCENT, h // 2)

        elif self.state == TUG:
            self._draw_bar()
            self._center_text("TAP YOUR BUTTON!", self.font_small, COLOR_TEXT, 60)

        elif self.state == RESULT:
            self._draw_bar()
            self._split_flash(self.winner)
            self._center_text(f"{self.winner} WINS THE TUG OF WAR!", self.font_mid, (255, 255, 255), h // 2)

        pygame.display.flip()

    def _draw_bar(self):
        w, h = self.screen.get_size()
        bar_x, bar_y = 80, h // 2 - 40
        bar_w, bar_h = w - 160, 80

        # background track
        pygame.draw.rect(self.screen, COLOR_BAR_BG, (bar_x, bar_y, bar_w, bar_h), border_radius=12)

        # left half (blue) filled up to center, right half (red) filled up to center
        center_x = bar_x + bar_w // 2
        pygame.draw.rect(self.screen, COLOR_LEFT, (bar_x, bar_y, bar_w // 2, bar_h), border_radius=12)
        pygame.draw.rect(self.screen, COLOR_RIGHT, (center_x, bar_y, bar_w // 2, bar_h), border_radius=12)

        # center line
        pygame.draw.line(self.screen, (255, 255, 255), (center_x, bar_y - 10), (center_x, bar_y + bar_h + 10), 3)

        # marker position: marker 0 -> far left edge, marker 100 -> far right edge
        ratio = self.marker / 100.0
        marker_x = int(bar_x + ratio * bar_w)
        pygame.draw.circle(self.screen, COLOR_MARKER, (marker_x, bar_y + bar_h // 2), 26)
        pygame.draw.circle(self.screen, (0, 0, 0), (marker_x, bar_y + bar_h // 2), 26, 3)

        # edge labels
        self._left_text("LEFT", self.font_small, COLOR_TEXT, bar_x, bar_y - 40)
        self._right_text("RIGHT", self.font_small, COLOR_TEXT, bar_x + bar_w, bar_y - 40)

    def _split_flash(self, winner_side):
        w, h = self.screen.get_size()
        overlay = pygame.Surface((w, h), pygame.SRCALPHA)
        if winner_side == "LEFT":
            overlay.fill((*COLOR_LEFT, 60))
            self.screen.blit(overlay, (0, 0), (0, 0, w // 2, h))
        elif winner_side == "RIGHT":
            overlay.fill((*COLOR_RIGHT, 60))
            self.screen.blit(overlay, (0, 0), (w // 2, 0, w // 2, h))

    def _center_text(self, text, font, color, y):
        w, _ = self.screen.get_size()
        surf = font.render(text, True, color)
        rect = surf.get_rect(center=(w // 2, y))
        self.screen.blit(surf, rect)

    def _left_text(self, text, font, color, x, y):
        surf = font.render(text, True, color)
        self.screen.blit(surf, (x, y))

    def _right_text(self, text, font, color, x, y):
        surf = font.render(text, True, color)
        rect = surf.get_rect()
        rect.right = x
        rect.top = y
        self.screen.blit(surf, rect)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sim", action="store_true",
                         help="Run without GPIO hardware; use A (left) / L (right) keys to simulate buttons")
    parser.add_argument("--fullscreen", action="store_true", help="Run in true fullscreen")
    args = parser.parse_args()

    hw = Hardware(simulate=args.sim)

    pygame.init()
    pygame.key.set_repeat()  # disable OS key-repeat so each keypress = one real tap
    flags = pygame.FULLSCREEN if args.fullscreen else 0
    screen = pygame.display.set_mode(SCREEN_SIZE, flags)
    pygame.display.set_caption("Tug of War")
    clock = pygame.time.Clock()

    game = Game(hw, screen, simulate=args.sim)

    sim_left_held = False
    sim_right_held = False

    running = True
    try:
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        running = False
                    elif args.sim:
                        if event.key in (pygame.K_LEFT, pygame.K_a):
                            sim_left_held = True
                        elif event.key in (pygame.K_RIGHT, pygame.K_l):
                            sim_right_held = True
                        hw.register_sim_tap(event.key)
                elif event.type == pygame.KEYUP:
                    if args.sim:
                        if event.key in (pygame.K_LEFT, pygame.K_a):
                            sim_left_held = False
                        elif event.key in (pygame.K_RIGHT, pygame.K_l):
                            sim_right_held = False

            if args.sim:
                game.set_sim_held(sim_left_held, sim_right_held)

            game.update()
            game.draw()
            clock.tick(FPS)
    finally:
        hw.cleanup()
        pygame.quit()


if __name__ == "__main__":
    main()