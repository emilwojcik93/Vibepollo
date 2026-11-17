/**
 * @file src/touch_ux.h
 * @brief Touch session UX: keyboard assist and taskbar auto-hide while a touch-primary client streams.
 *
 * Injected touch does not trigger the Windows touch keyboard or the tablet taskbar behavior the
 * way a real touch screen does. While at least one touch-primary session is active the host
 * (1) shows the touch keyboard when a text field gets focus right after touch input, hides it
 * again when focus leaves text fields, and (2) auto-hides the taskbar, restoring the previous
 * state when the last touch session ends. Both parts are individually configurable
 * (`touch_keyboard_assist`, `touch_taskbar_autohide`). Other platforms: no-ops.
 */
#pragma once

namespace touch_ux {
#ifdef _WIN32
  /// A touch-primary session started. Calls are reference counted; the first one starts the helper.
  void acquire();

  /// A touch-primary session ended. The last release stops the helper and restores the taskbar.
  void release();

  /// Injected touch just happened (cheap, thread-safe).
  void note_touch();

  /// Restore the taskbar if a previous host process died while it was auto-hidden for touch.
  void recover();
#else
  inline void acquire() {}

  inline void release() {}

  inline void note_touch() {}

  inline void recover() {}
#endif
}  // namespace touch_ux
