#!/usr/bin/env python3
"""
Two-Player Tug-of-War Game — Raspberry Pi 5
=============================================

Hardware — same wiring style as reaction_game.py:
  Buzzer (active buzzer)  -> GPIO18 -> GND
  Monitor                 -> HDMI (pygame fullscreen)

  >>> No physical buttons needed. Both players use the keyboard:
        LEFT SHIFT  -> LEFT player  [ = BLUE side on the bar ]
        RIGHT SHIFT -> RIGHT player [ = RED side on the bar  ]
      Hold both to arm a round, then tap (press) your key repeatedly
      during TUG to pull. This works the same with real GPIO (buzzer)
      or with --sim. The old physical-button wiring (GPIO17/GPIO27) is
      left commented in the Config section below in case you want to
      wire real arcade buttons back in later.

  >>> LEDs are currently disabled. The wiring + control code is still
      here, just commented out (see LED_PINS and the Hardware class),
      so you can re-enable them any time by uncommenting.

Flow:
  1. WAIT_HOLD : both players hold LEFT SHIFT + RIGHT SHIFT together to
                 arm the round.
  2. HOLDING   : hold for 3 seconds (on-screen dots fill in).
  3. GO        : buzzer beeps, screen flashes "PULL!" and the tug bar
                 starts as an even 50/50 blue/red split.
  4. TUG       : each key TAP (press edge, not hold) pulls the color
                 boundary toward your side. Instead of a ball/marker,
                 your color itself visibly grows and creeps across the
                 bar as you win ground — moving diagonal stripes animate
                 through the leading color so it's obvious in real time
                 which side is taking over. The boundary also slowly
                 drifts back to center every frame, so mashing
                 continuously is required to keep winning — a single tap
                 won't hold it.
  5. RESULT    : whoever's color fully covers the bar wins. Split-screen
                 flash on the monitor + buzzer fanfare, then auto-resets
                 to WAIT_HOLD.

Install deps:
  pip install gpiozero pygame --break-system-packages

Run:
  python3 tug_of_war.py
  (add --sim to run without real GPIO hardware — buzzer becomes a no-op;
  keyboard controls are always active either way)
  Controls: hold LEFT SHIFT + RIGHT SHIFT to arm, then mash your own
            SHIFT key to pull (tap repeatedly — key-repeat is disabled
            so each physical keypress = one tap, just like a real button)
"""

import os
import sys
import time
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

# Tug-of-war tuning
PULL_PER_TAP = 4.0        # how much one tap moves the marker (in %, 0-100 scale)
DECAY_PER_SECOND = 6.0    # how fast the marker drifts back to center each second
MARKER_START = 50.0       # center of the bar
MARKER_MIN = 0.0          # LEFT wins here
MARKER_MAX = 100.0        # RIGHT wins here

SCREEN_SIZE = (1024, 600)
FPS = 60

# Glitch-style display font (Rubik Glitch, SIL Open Font License).
# Bundled in fonts/RubikGlitch-Regular.ttf next to this script. Falls
# back to the default system font automatically if the file is missing.
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
GLITCH_FONT_PATH = os.path.join(FONT_DIR, "RubikGlitch-Regular.ttf")

COLOR_BG = (15, 15, 20)
COLOR_LEFT = (40, 120, 220)
COLOR_RIGHT = (220, 60, 60)
COLOR_TEXT = (240, 240, 240)
COLOR_ACCENT = (250, 210, 60)
COLOR_GO_GREEN = (34, 197, 94)
COLOR_BAR_BG = (40, 40, 46)
COLOR_MARKER = (255, 255, 255)

# ---------------------------------------------------------------------------
# Hardware layer — real GPIO or keyboard simulation
# ---------------------------------------------------------------------------
class Hardware:
    """Wraps gpiozero (buzzer only) plus keyboard input. Player input is
    always the keyboard — LEFT SHIFT / RIGHT SHIFT — no physical
    buttons required, on real hardware or with --sim."""

    def __init__(self, simulate=False):
        self.simulate = simulate
        self._left_held = False
        self._right_held = False
        self._left_taps = 0
        self._right_taps = 0

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

    # --- raw held-state (used for the "hold both to start" phase) ---
    def left_held(self):
        return self._left_held

    def right_held(self):
        return self._right_held

    def set_key(self, key, is_down):
        """Call on every KEYDOWN/KEYUP for LEFT SHIFT / RIGHT SHIFT.
        Every KEYDOWN also counts as one tap, since OS key-repeat is
        disabled in main() so each physical keypress fires exactly once."""
        if key == pygame.K_LSHIFT:
            self._left_held = is_down
            if is_down:
                self._left_taps += 1
        elif key == pygame.K_RSHIFT:
            self._right_held = is_down
            if is_down:
                self._right_taps += 1

    # --- tap edge detection: returns number of NEW presses since last call ---
    def poll_taps(self):
        """Returns (left_taps, right_taps) counted since the last call."""
        lt, rt = self._left_taps, self._right_taps
        self._left_taps = 0
        self._right_taps = 0
        return lt, rt

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

    def leds_blink(self, times=3, interval=0.12):
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
TUG = "TUG"
RESULT = "RESULT"


class Game:
    def __init__(self, hw: Hardware, screen):
        self.hw = hw
        self.screen = screen
        self.font_big, self.font_mid, self.font_small = self._load_fonts()
        # Clean (non-glitch) fonts used only for the HOLD countdown, since
        # that's the one spot players need to read a changing count at a
        # glance -- glitch styling there just makes it harder to track.
        self.font_clean_big = pygame.font.SysFont("Arial", 90, bold=True)
        self.font_clean_mid = pygame.font.SysFont("Arial", 46, bold=True)
        self.reset_to_wait()

    @staticmethod
    def _load_fonts():
        """Try the bundled glitch font first; fall back to a bold system
        font if it's missing so the game never crashes over a font file."""
        try:
            if os.path.isfile(GLITCH_FONT_PATH):
                return (
                    pygame.font.Font(GLITCH_FONT_PATH, 90),
                    pygame.font.Font(GLITCH_FONT_PATH, 46),
                    pygame.font.Font(GLITCH_FONT_PATH, 26),
                )
        except Exception as e:
            print(f"[fonts] couldn't load glitch font ({e}), falling back to system font")
        return (
            pygame.font.SysFont("Arial", 90, bold=True),
            pygame.font.SysFont("Arial", 46, bold=True),
            pygame.font.SysFont("Arial", 26),
        )

    def reset_to_wait(self):
        self.state = WAIT_HOLD
        self.hold_start_time = None
        self.leds_lit = 0
        self.marker = MARKER_START
        self.winner = None
        self.result_start_time = None
        self.last_frame_time = time.time()
        self.anim_time = 0.0  # drives the moving stripes on the leading color
        self.hw.leds_all(False)
        # drain any queued taps so they don't leak into next round
        self.hw.poll_taps()

    # ---- state handlers -------------------------------------------------
    def update(self):
        now = time.time()
        dt = now - self.last_frame_time
        self.last_frame_time = now
        self.anim_time += dt

        if self.state == WAIT_HOLD:
            self.hw.poll_taps()  # discard stray taps
            if self.hw.left_held() and self.hw.right_held():
                self.state = HOLDING
                self.hold_start_time = now
                self.leds_lit = 0
                self.hw.leds_set(0)

        elif self.state == HOLDING:
            if not (self.hw.left_held() and self.hw.right_held()):
                self.reset_to_wait()
                return
            elapsed = now - self.hold_start_time
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

            # on-screen "progress dots" show how close the LEADING side is
            # to winning (0..HOLD_STEPS), independent of physical LEDs
            n = HOLD_STEPS
            distance_from_center = abs(self.marker - MARKER_START)
            max_distance = MARKER_START - MARKER_MIN  # symmetric
            progress_ratio = distance_from_center / max_distance if max_distance else 0
            lit = min(n, int(progress_ratio * n))
            if lit != self.leds_lit:
                self.leds_lit = lit
                self.hw.leds_set(lit)  # no-op while LEDs are disabled
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
            self._center_text("HOLD...", self.font_clean_mid, COLOR_TEXT, h // 2 - 80)
            n = HOLD_STEPS
            dots = "● " * self.leds_lit + "○ " * (n - self.leds_lit)
            self._center_text(dots, self.font_clean_big, COLOR_ACCENT, h // 2 + 10)

        elif self.state == GO:
            self.screen.fill(COLOR_GO_GREEN)
            self._center_text("PULL!!", self.font_big, (255, 255, 255), h // 2)

        elif self.state == TUG:
            self._draw_bar()
            self._center_text("MASH YOUR SHIFT KEY!", self.font_small, COLOR_TEXT, 60)

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

        # Instead of a fixed 50/50 split + a ball marker, the color boundary
        # itself moves: as LEFT (blue) pulls, marker drops toward MARKER_MIN
        # and blue visibly swallows more of the bar; as RIGHT (red) pulls,
        # marker rises toward MARKER_MAX and red swallows more of the bar.
        ratio = self.marker / 100.0                    # 0 = LEFT/blue wins, 1 = RIGHT/red wins
        boundary_x = bar_x + int((1.0 - ratio) * bar_w)  # blue occupies [bar_x, boundary_x)
        blue_w = boundary_x - bar_x
        red_w = bar_w - blue_w

        # clip to the rounded track so the fills don't spill past the corners
        clip_rect = pygame.Rect(bar_x, bar_y, bar_w, bar_h)
        prev_clip = self.screen.get_clip()
        self.screen.set_clip(clip_rect)

        if blue_w > 0:
            pygame.draw.rect(self.screen, COLOR_LEFT, (bar_x, bar_y, blue_w, bar_h))
            self._draw_moving_stripes((bar_x, bar_y, blue_w, bar_h), direction=1)
        if red_w > 0:
            pygame.draw.rect(self.screen, COLOR_RIGHT, (boundary_x, bar_y, red_w, bar_h))
            self._draw_moving_stripes((boundary_x, bar_y, red_w, bar_h), direction=-1)

        self.screen.set_clip(prev_clip)
        pygame.draw.rect(self.screen, (0, 0, 0), (bar_x, bar_y, bar_w, bar_h), width=3, border_radius=12)

        # bright "tug line" at the live boundary between the two colors,
        # with a gentle pulse so it reads as active/moving even if no one
        # has tapped in the last instant
        pulse = 4 + int(3 * abs(pygame.math.Vector2(1, 0).rotate(self.anim_time * 220).x))
        pygame.draw.line(self.screen, COLOR_MARKER,
                          (boundary_x, bar_y - 12), (boundary_x, bar_y + bar_h + 12), pulse)

        # center reference line (where the boundary started)
        center_x = bar_x + bar_w // 2
        pygame.draw.line(self.screen, (255, 255, 255, 120), (center_x, bar_y - 6), (center_x, bar_y + bar_h + 6), 1)

        # edge labels, colored to match each side so it's obvious at a glance
        self._left_text("LEFT SHIFT (BLUE)", self.font_small, COLOR_LEFT, bar_x, bar_y - 40)
        self._right_text("RIGHT SHIFT (RED)", self.font_small, COLOR_RIGHT, bar_x + bar_w, bar_y - 40)

    def _draw_moving_stripes(self, rect, direction):
        """Diagonal stripes that scroll through a colored region over time,
        so the leading color visibly 'moves' rather than sitting static —
        this is what shows which side is actively taking over the bar."""
        x, y, rw, rh = rect
        if rw <= 0:
            return
        stripe_gap = 34
        speed = 90  # pixels/second of scroll
        offset = (self.anim_time * speed * direction) % stripe_gap
        overlay_color = (255, 255, 255, 26)
        overlay = pygame.Surface((rw, rh), pygame.SRCALPHA)
        start = -stripe_gap + offset
        sx = start
        while sx < rw + rh:
            pygame.draw.line(overlay, overlay_color, (sx, rh), (sx + rh, 0), 10)
            sx += stripe_gap
        self.screen.blit(overlay, (x, y))

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
                         help="Run without real GPIO hardware (buzzer becomes a no-op). "
                              "Keyboard controls (LEFT SHIFT / RIGHT SHIFT) work either way.")
    parser.add_argument("--fullscreen", action="store_true", help="Run in true fullscreen")
    args = parser.parse_args()

    hw = Hardware(simulate=args.sim)

    pygame.init()
    pygame.key.set_repeat()  # disable OS key-repeat so each keypress = one real tap
    flags = pygame.FULLSCREEN if args.fullscreen else 0
    screen = pygame.display.set_mode(SCREEN_SIZE, flags)
    pygame.display.set_caption("Tug of War")
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