#!/usr/bin/env python3
"""
Two-Player Reaction Game — Raspberry Pi 5
==========================================

Hardware:
  Buzzer (active buzzer)  -> GPIO18 -> GND
  Monitor                 -> HDMI (pygame fullscreen)

  >>> No physical buttons needed. Both players use the keyboard:
        LEFT SHIFT  -> LEFT player  [ = BLUE side on screen ]
        RIGHT SHIFT -> RIGHT player [ = RED side on screen  ]
      This works the same whether you run with real GPIO (buzzer) or
      with --sim. The old physical-button wiring (GPIO17/GPIO27) is
      left commented in the Config section below in case you want to
      wire real arcade buttons back in later.

  >>> LEDs are currently disabled. The wiring + control code is still
      here, just commented out (see LED_PINS and the Hardware class),
      so you can re-enable them any time by uncommenting.

Flow:
  1. WAIT_HOLD   : both players must hold their button down together.
  2. HOLDING     : once both are held, count 3 seconds; light one more
                   LED each second. Releasing early cancels and resets.
  3. GO          : all LEDs lit, buzzer beeps, screen shows "GAME START".
  4. ROULETTE    : the monitor spins rapidly through numbers 1-10 like a
                   slot machine (buzzer ticks each flip), gradually
                   slowing down until it lands on the random time.
                   Pressing your button here = FALSE START = you lose.
  5. COUNTDOWN   : the number it landed on is now shown counting down to
                   0 on screen, one second at a time, buzzer ticking
                   each second to build pressure. Pressing early here
                   still = FALSE START = you lose.
  6. LIVE        : the instant the countdown hits 0, first press wins.
  7. RESULT      : split-screen shows the winning side, LEDs flash on
                   winner's side, buzzer plays a short win sound.
                   After a few seconds, resets to WAIT_HOLD.

Install deps:
  pip install gpiozero pygame --break-system-packages

Run:
  python3 reaction_game.py
  (add --sim to run without real GPIO hardware — buzzer becomes a no-op;
  keyboard controls are always active either way)
  Controls: LEFT SHIFT = LEFT/BLUE player, RIGHT SHIFT = RIGHT/RED player
"""

import sys
import time
import random
import argparse

import pygame

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# Buttons are no longer needed -- both players use the keyboard instead
# (LEFT SHIFT / RIGHT SHIFT, see Hardware.set_key below). Kept here,
# commented, in case you want to wire real arcade buttons back in later.
# BTN_LEFT_PIN = 17
# BTN_RIGHT_PIN = 27

# LEDs are currently disabled. Uncomment LED_PINS below (and the LED
# lines inside the Hardware class) to bring them back -- all LED logic
# already scales off len(LED_PINS), so nothing else needs to change.
# LED_PINS = [
#     5,
#     6,
#     13,
# ]
LED_PINS = []
BUZZER_PIN = 18

HOLD_SECONDS = 3.0
HOLD_STEPS = 3      # on-screen "progress dots" while holding, independent of LEDs
RESULT_DISPLAY_SECONDS = 4.0

# Roulette (the "spinning number" reveal of the random countdown time)
MIN_SUSPENSE_INT = 1
MAX_SUSPENSE_INT = 10
ROULETTE_MIN_FLIPS = 14        # minimum number of number-changes before landing
ROULETTE_MAX_FLIPS = 20
ROULETTE_START_INTERVAL = 0.05  # seconds between flips at the start (fast)
ROULETTE_END_INTERVAL = 0.35    # seconds between flips right before landing (slow)

SCREEN_SIZE = (1024, 600)  # change to match your monitor, or use (0,0) for native fullscreen
FPS = 60

COLOR_BG = (15, 15, 20)
COLOR_LEFT = (40, 120, 220)
COLOR_RIGHT = (220, 60, 60)
COLOR_TEXT = (240, 240, 240)
COLOR_GO_GREEN = (34, 197, 94)
COLOR_ACCENT = (250, 210, 60)

# ---------------------------------------------------------------------------
# Hardware layer — real GPIO or keyboard simulation
# ---------------------------------------------------------------------------
class Hardware:
    """Wraps gpiozero (buzzer only) plus keyboard input, so the rest of
    the code doesn't care whether real GPIO hardware is present. Player
    input is always the keyboard — LEFT SHIFT / RIGHT SHIFT — no
    physical buttons required, on real hardware or with --sim."""

    def __init__(self, simulate=False):
        self.simulate = simulate
        self._left = False
        self._right = False

        self.num_leds = len(LED_PINS)  # 0 while LEDs are disabled

        if not simulate:
            from gpiozero import Buzzer
            # from gpiozero import LED
            # self.leds = [LED(pin) for pin in LED_PINS]
            self.leds = None
            self.buzzer = Buzzer(BUZZER_PIN)
        else:
            self.leds = None
            self.buzzer = None

    # --- button state (keyboard: LEFT SHIFT / RIGHT SHIFT) ---
    def left_pressed(self):
        return self._left

    def right_pressed(self):
        return self._right

    def set_key(self, key, is_down):
        if key == pygame.K_LSHIFT:
            self._left = is_down
        elif key == pygame.K_RSHIFT:
            self._right = is_down

    # --- LEDs (disabled -- uncomment LED_PINS above + the lines below
    # to re-enable; everything that calls these already works fine
    # with them as no-ops) ---
    def leds_set(self, count_on):
        pass
        # if self.simulate or not self.leds:
        #     return
        # for i, led in enumerate(self.leds):
        #     led.value = 1 if i < count_on else 0

    def leds_all(self, on):
        pass
        # self.leds_set(self.num_leds if on else 0)

    def leds_blink(self, times=3, interval=0.15):
        pass
        # if self.simulate or not self.leds:
        #     return
        # for _ in range(times):
        #     for led in self.leds:
        #         led.on()
        #     time.sleep(interval)
        #     for led in self.leds:
        #         led.off()
        #     time.sleep(interval)

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
            # if self.leds:
            #     for led in self.leds:
            #         led.off()
            self.buzzer.off()


# ---------------------------------------------------------------------------
# Game states
# ---------------------------------------------------------------------------
WAIT_HOLD = "WAIT_HOLD"
HOLDING = "HOLDING"
GO = "GO"
ROULETTE = "ROULETTE"
COUNTDOWN = "COUNTDOWN"
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
        self.roulette_display = None
        self.roulette_final = None
        self.roulette_flip_count = 0
        self.roulette_total_flips = 0
        self.roulette_next_change_time = None
        self.countdown_target = None
        self.countdown_remaining = None
        self.last_countdown_tick = None
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
            n = HOLD_STEPS
            # spread the progress-dot lighting evenly across HOLD_SECONDS
            step = HOLD_SECONDS / n
            target_lit = min(n, int(elapsed // step) + 1) if elapsed < HOLD_SECONDS else n
            if target_lit != self.leds_lit:
                self.leds_lit = target_lit
                self.hw.leds_set(self.leds_lit)  # no-op while LEDs are disabled
            if elapsed >= HOLD_SECONDS:
                self.state = GO
                self.hw.leds_all(True)
                self.hw.beep(0.3)
                self.go_shown_at = time.time()

        elif self.state == GO:
            # brief "GAME START" splash, then move to the roulette spin
            if time.time() - self.go_shown_at >= 0.8:
                self._start_roulette()

        elif self.state == ROULETTE:
            # false start check -> pressing during the spin loses instantly
            if left or right:
                self.winner = "RIGHT" if left and not right else \
                               "LEFT" if right and not left else None
                if self.winner is None:
                    self.reset_to_wait()
                    return
                self._enter_result(false_start=True)
                return

            now = time.time()
            if now >= self.roulette_next_change_time:
                self.roulette_flip_count += 1
                if self.roulette_flip_count >= self.roulette_total_flips:
                    # landed on the final number
                    self.roulette_display = self.roulette_final
                    self.hw.beep(0.15)
                    self.countdown_target = self.roulette_final
                    self.countdown_remaining = self.roulette_final
                    self.last_countdown_tick = now
                    self.state = COUNTDOWN
                else:
                    # keep spinning through random numbers, slowing down
                    # (ease-out) as it approaches the final flip
                    self.roulette_display = random.randint(MIN_SUSPENSE_INT, MAX_SUSPENSE_INT)
                    self.hw.beep_async_tick()
                    progress = self.roulette_flip_count / self.roulette_total_flips
                    interval = ROULETTE_START_INTERVAL + progress * (
                        ROULETTE_END_INTERVAL - ROULETTE_START_INTERVAL
                    )
                    self.roulette_next_change_time = now + interval

        elif self.state == COUNTDOWN:
            # false start check -> pressing during the countdown loses instantly
            if left or right:
                self.winner = "RIGHT" if left and not right else \
                               "LEFT" if right and not left else None
                if self.winner is None:
                    self.reset_to_wait()
                    return
                self._enter_result(false_start=True)
                return

            now = time.time()
            if now - self.last_countdown_tick >= 1.0:
                self.last_countdown_tick = now
                self.countdown_remaining -= 1
                if self.countdown_remaining > 0:
                    self.hw.beep_async_tick()
                else:
                    self.state = LIVE
                    self.live_start_time = now
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

    def _start_roulette(self):
        self.roulette_final = random.randint(MIN_SUSPENSE_INT, MAX_SUSPENSE_INT)
        self.roulette_display = random.randint(MIN_SUSPENSE_INT, MAX_SUSPENSE_INT)
        self.roulette_flip_count = 0
        self.roulette_total_flips = random.randint(ROULETTE_MIN_FLIPS, ROULETTE_MAX_FLIPS)
        self.roulette_next_change_time = time.time() + ROULETTE_START_INTERVAL
        self.state = ROULETTE

    def _enter_result(self, false_start):
        self.false_start = false_start
        self.state = RESULT
        self.result_start_time = time.time()
        self.hw.leds_blink(times=3, interval=0.12)
        self.hw.leds_all(True)
        self.hw.win_fanfare()

    # ---- rendering --------------------------------------------------------
    def draw(self):
        w, h = self.screen.get_size()
        self.screen.fill(COLOR_BG)

        if self.state == WAIT_HOLD:
            self._center_text("HOLD LEFT SHIFT + RIGHT SHIFT TO START", self.font_mid, COLOR_TEXT, h // 2 - 40)
            self._center_text("LEFT SHIFT = BLUE side", self.font_small, COLOR_LEFT, h // 2 + 30)
            self._center_text("RIGHT SHIFT = RED side", self.font_small, COLOR_RIGHT, h // 2 + 65)

        elif self.state == HOLDING:
            elapsed = time.time() - self.hold_start_time
            self._center_text("HOLD...", self.font_mid, COLOR_TEXT, h // 2 - 80)
            n = HOLD_STEPS
            dots = "● " * self.leds_lit + "○ " * (n - self.leds_lit)
            self._center_text(dots, self.font_big, COLOR_ACCENT, h // 2 + 10)

        elif self.state == GO:
            self.screen.fill(COLOR_GO_GREEN)
            self._center_text("GAME START!", self.font_big, (255, 255, 255), h // 2)

        elif self.state == ROULETTE:
            self._center_text("SELECTING TIME...", self.font_small, COLOR_TEXT, h // 2 - 120)
            self._center_text(str(self.roulette_display), self.font_big, COLOR_ACCENT, h // 2 + 10)

        elif self.state == COUNTDOWN:
            self._center_text("GET READY...", self.font_small, COLOR_TEXT, h // 2 - 120)
            self._center_text(str(self.countdown_remaining), self.font_big, (200, 30, 30), h // 2 + 10)

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
        self._left_text("BLUE = LEFT SHIFT", self.font_small, (255, 255, 255), 20, 20)
        self._right_text("RED = RIGHT SHIFT", self.font_small, (255, 255, 255), w - 20, 20)

    def _left_text(self, text, font, color, x, y):
        surf = font.render(text, True, color)
        self.screen.blit(surf, (x, y))

    def _right_text(self, text, font, color, x, y):
        surf = font.render(text, True, color)
        rect = surf.get_rect()
        rect.right = x
        rect.top = y
        self.screen.blit(surf, rect)

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
                         help="Run without real GPIO hardware (buzzer becomes a no-op). "
                              "Keyboard controls (LEFT SHIFT / RIGHT SHIFT) work either way.")
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
                    else:
                        hw.set_key(event.key, True)
                elif event.type == pygame.KEYUP:
                    hw.set_key(event.key, False)

            game.update()
            game.draw()
            clock.tick(FPS)
    finally:
        hw.cleanup()
        pygame.quit()


if __name__ == "__main__":
    main()