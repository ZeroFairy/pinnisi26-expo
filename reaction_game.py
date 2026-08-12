#!/usr/bin/env python3
"""
Two-Player Reaction Game — Raspberry Pi 5
==========================================

Hardware (BCM pin numbers):
  Button 1 (Player LEFT)  -> GPIO17 -> GND
  Button 2 (Player RIGHT) -> GPIO27 -> GND
  LED 1                   -> GPIO5  (+resistor) -> GND
  LED 2                   -> GPIO6  (+resistor) -> GND
  LED 3                   -> GPIO13 (+resistor) -> GND
  Buzzer (active buzzer)  -> GPIO18 -> GND
  Monitor                 -> HDMI (pygame fullscreen)

Flow:
  1. WAIT_HOLD   : both players must hold their button down together.
  2. HOLDING     : once both are held, count 3 seconds; light one more
                   LED each second. Releasing early cancels and resets.
  3. GO          : all 3 LEDs lit, buzzer beeps, screen shows "GAME START".
  4. SUSPENSE    : pick a random delay (1-10s), show a countdown on screen.
                   Buzzer ticks each second to build pressure.
                   Pressing your button here = FALSE START = you lose.
  5. LIVE        : the instant the countdown hits 0, first press wins.
  6. RESULT      : split-screen shows the winning side, LEDs flash on
                   winner's side, buzzer plays a short win sound.
                   After a few seconds, resets to WAIT_HOLD.

Install deps:
  pip install gpiozero pygame --break-system-packages

Run:
  python3 reaction_game.py
  (add --sim to run without real GPIO hardware, using keyboard L/R keys)
"""

import sys
import time
import random
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
MIN_SUSPENSE = 1.0
MAX_SUSPENSE = 10.0
RESULT_DISPLAY_SECONDS = 4.0

SCREEN_SIZE = (1024, 600)  # change to match your monitor, or use (0,0) for native fullscreen
FPS = 60

COLOR_BG = (15, 15, 20)
COLOR_LEFT = (40, 120, 220)
COLOR_RIGHT = (220, 60, 60)
COLOR_TEXT = (240, 240, 240)
COLOR_ACCENT = (250, 210, 60)

# ---------------------------------------------------------------------------
# Hardware layer — real GPIO or keyboard simulation
# ---------------------------------------------------------------------------
class Hardware:
    """Wraps gpiozero so the rest of the code doesn't care if we're on
    real hardware or running --sim on a dev machine with a keyboard."""

    def __init__(self, simulate=False):
        self.simulate = simulate
        self._sim_left = False
        self._sim_right = False

        self.num_leds = len(LED_PINS)  # everything below scales off this

        if not simulate:
            from gpiozero import Button, LED, Buzzer
            self.btn_left = Button(BTN_LEFT_PIN, pull_up=True, bounce_time=0.02)
            self.btn_right = Button(BTN_RIGHT_PIN, pull_up=True, bounce_time=0.02)
            self.leds = [LED(pin) for pin in LED_PINS]
            self.buzzer = Buzzer(BUZZER_PIN)
        else:
            self.btn_left = None
            self.btn_right = None
            self.leds = None
            self.buzzer = None

    # --- button state ---
    def left_pressed(self):
        if self.simulate:
            return self._sim_left
        return self.btn_left.is_pressed

    def right_pressed(self):
        if self.simulate:
            return self._sim_right
        return self.btn_right.is_pressed

    def set_sim_key(self, key, is_down):
        if key == pygame.K_LEFT or key == pygame.K_a:
            self._sim_left = is_down
        elif key == pygame.K_RIGHT or key == pygame.K_l:
            self._sim_right = is_down

    # --- LEDs ---
    def leds_set(self, count_on):
        """Light up the first `count_on` LEDs (0..num_leds)."""
        if self.simulate:
            return
        for i, led in enumerate(self.leds):
            led.value = 1 if i < count_on else 0

    def leds_all(self, on):
        self.leds_set(self.num_leds if on else 0)

    def leds_blink(self, times=3, interval=0.15):
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

    def beep_async_tick(self):
        """Short tick, non-blocking-ish (call from main loop at the right time)."""
        if self.simulate:
            return
        self.buzzer.on()
        time.sleep(0.05)
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
SUSPENSE = "SUSPENSE"
LIVE = "LIVE"
RESULT = "RESULT"


class Game:
    def __init__(self, hw: Hardware, screen):
        self.hw = hw
        self.screen = screen
        self.font_big = pygame.font.SysFont("Arial", 96, bold=True)
        self.font_mid = pygame.font.SysFont("Arial", 48, bold=True)
        self.font_small = pygame.font.SysFont("Arial", 28)
        self.reset_to_wait()

    def reset_to_wait(self):
        self.state = WAIT_HOLD
        self.hold_start_time = None
        self.leds_lit = 0
        self.suspense_target = None
        self.suspense_start = None
        self.last_tick_second = None
        self.live_start_time = None
        self.winner = None
        self.result_start_time = None
        self.hw.leds_all(False)

    # ---- state handlers -------------------------------------------------
    def update(self):
        left = self.hw.left_pressed()
        right = self.hw.right_pressed()

        if self.state == WAIT_HOLD:
            if left and right:
                self.state = HOLDING
                self.hold_start_time = time.time()
                self.leds_lit = 0
                self.hw.leds_set(0)

        elif self.state == HOLDING:
            if not (left and right):
                # someone let go early -> reset
                self.reset_to_wait()
                return
            elapsed = time.time() - self.hold_start_time
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
                self.go_shown_at = time.time()

        elif self.state == GO:
            # brief "GAME START" splash, then move to suspense
            if time.time() - self.go_shown_at >= 0.8:
                self.suspense_target = random.uniform(MIN_SUSPENSE, MAX_SUSPENSE)
                self.suspense_start = time.time()
                self.last_tick_second = None
                self.state = SUSPENSE

        elif self.state == SUSPENSE:
            # false start check
            if left or right:
                self.winner = "RIGHT" if left and not right else \
                               "LEFT" if right and not left else None
                # if both pressed simultaneously somehow, treat as no-winner/tie -> just restart
                if self.winner is None:
                    self.reset_to_wait()
                    return
                self._enter_result(false_start=True)
                return

            elapsed = time.time() - self.suspense_start
            remaining = self.suspense_target - elapsed
            sec_left = max(0, int(remaining) + 1)
            if sec_left != self.last_tick_second:
                self.last_tick_second = sec_left
                self.hw.beep_async_tick()

            if elapsed >= self.suspense_target:
                self.state = LIVE
                self.live_start_time = time.time()
                self.hw.leds_all(False)

        elif self.state == LIVE:
            if left and right:
                self.winner = None  # tie / simultaneous, no clean winner
                self.reset_to_wait()
                return
            if left:
                self.winner = "LEFT"
                self._enter_result(false_start=False)
            elif right:
                self.winner = "RIGHT"
                self._enter_result(false_start=False)

        elif self.state == RESULT:
            if time.time() - self.result_start_time >= RESULT_DISPLAY_SECONDS:
                self.reset_to_wait()

    def _enter_result(self, false_start):
        self.false_start = false_start
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
            elapsed = time.time() - self.hold_start_time
            self._center_text("HOLD...", self.font_mid, COLOR_TEXT, h // 2 - 80)
            n = self.hw.num_leds
            dots = "● " * self.leds_lit + "○ " * (n - self.leds_lit)
            self._center_text(dots, self.font_big, COLOR_ACCENT, h // 2 + 10)

        elif self.state == GO:
            self._center_text("GAME START!", self.font_big, COLOR_ACCENT, h // 2)

        elif self.state == SUSPENSE:
            self._center_text("GET READY...", self.font_mid, COLOR_TEXT, h // 2 - 40)
            self._center_text("!", self.font_big, (200, 30, 30), h // 2 + 40)

        elif self.state == LIVE:
            self._split_screen(None)
            self._center_text("GO!!", self.font_big, (255, 255, 255), h // 2)

        elif self.state == RESULT:
            self._split_screen(self.winner)
            label = f"{self.winner} WINS!"
            if self.false_start:
                label = f"{self.winner_loses_label()} FALSE START — {self.winner} WINS!"
            self._center_text(label, self.font_mid, (255, 255, 255), h // 2)

        pygame.display.flip()

    def winner_loses_label(self):
        return "RIGHT" if self.winner == "LEFT" else "LEFT"

    def _split_screen(self, winner_side):
        w, h = self.screen.get_size()
        left_color = COLOR_LEFT
        right_color = COLOR_RIGHT
        if winner_side == "LEFT":
            right_color = tuple(c // 3 for c in COLOR_RIGHT)
        elif winner_side == "RIGHT":
            left_color = tuple(c // 3 for c in COLOR_LEFT)
        pygame.draw.rect(self.screen, left_color, (0, 0, w // 2, h))
        pygame.draw.rect(self.screen, right_color, (w // 2, 0, w // 2, h))

    def _center_text(self, text, font, color, y):
        w, _ = self.screen.get_size()
        surf = font.render(text, True, color)
        rect = surf.get_rect(center=(w // 2, y))
        self.screen.blit(surf, rect)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sim", action="store_true",
                         help="Run without GPIO hardware; use arrow keys / A-L to simulate buttons")
    parser.add_argument("--fullscreen", action="store_true", help="Run in true fullscreen")
    args = parser.parse_args()

    hw = Hardware(simulate=args.sim)

    pygame.init()
    flags = pygame.FULLSCREEN if args.fullscreen else 0
    screen = pygame.display.set_mode(SCREEN_SIZE, flags)
    pygame.display.set_caption("Reaction Duel")
    clock = pygame.time.Clock()

    game = Game(hw, screen)

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
                        hw.set_sim_key(event.key, True)
                elif event.type == pygame.KEYUP:
                    if args.sim:
                        hw.set_sim_key(event.key, False)

            game.update()
            game.draw()
            clock.tick(FPS)
    finally:
        hw.cleanup()
        pygame.quit()


if __name__ == "__main__":
    main()