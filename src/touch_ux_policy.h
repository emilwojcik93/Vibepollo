/**
 * @file src/touch_ux_policy.h
 * @brief Pure decision rules for the touch session UX helper (touch keyboard assist).
 *
 * Windows only shows the touch keyboard for focus that came from a physical touch device, so
 * touch that the host injects on behalf of a streaming client never brings it up. The helper
 * watches keyboard focus and asks the shell to show or hide the keyboard. This header holds the
 * decisions so they can be tested without Windows.
 */
#pragma once

#include <chrono>

namespace touch_ux {
  /// How recent injected touch must be for a focus change to count as "caused by touch".
  inline constexpr std::chrono::milliseconds touch_recent_window {2500};

  /// Delay before the keyboard is hidden after focus left every text field; absorbs focus flicker.
  inline constexpr std::chrono::milliseconds hide_delay {350};

  enum class osk_action_e {
    none,  ///< Leave the keyboard alone.
    show,  ///< Ask the shell to show the keyboard.
    schedule_hide,  ///< Hide it after hide_delay unless a text field gets focus again.
    forget,  ///< The user closed the keyboard themselves; stop tracking it.
  };

  struct focus_state_t {
    bool editable = false;  ///< The focused element accepts text.
    bool osk_visible = false;  ///< The touch keyboard currently occupies screen space.
    bool we_showed = false;  ///< This helper showed the keyboard and has not hidden it since.
    bool touch_recent = false;  ///< Touch was injected within touch_recent_window.
  };

  /// Decide what to do for one focus change.
  constexpr osk_action_e decide(const focus_state_t &state) {
    if (state.editable) {
      return (!state.osk_visible && state.touch_recent) ? osk_action_e::show : osk_action_e::none;
    }
    if (state.we_showed) {
      return state.osk_visible ? osk_action_e::schedule_hide : osk_action_e::forget;
    }
    return osk_action_e::none;
  }

  /// True when `last_touch` is inside the recent window at `now`.
  constexpr bool touch_is_recent(std::chrono::steady_clock::time_point last_touch, std::chrono::steady_clock::time_point now) {
    return last_touch != std::chrono::steady_clock::time_point {} && now >= last_touch && now - last_touch <= touch_recent_window;
  }

  /// New taskbar state flags (ABS_* bits) for a touch session: same flags plus auto-hide.
  constexpr unsigned int taskbar_state_for_touch(unsigned int current, unsigned int autohide_bit) {
    return current | autohide_bit;
  }

  /// Taskbar state flags to restore when the touch session ends.
  constexpr unsigned int taskbar_state_restored(unsigned int current, unsigned int autohide_bit, bool autohide_was_on) {
    return autohide_was_on ? (current | autohide_bit) : (current & ~autohide_bit);
  }
}  // namespace touch_ux
