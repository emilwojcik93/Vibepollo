#include "../tests_common.h"

#include "src/touch_ux_policy.h"

namespace tu = touch_ux;
using action_e = tu::osk_action_e;
using clock_t_ = std::chrono::steady_clock;

TEST(TouchUxDecide, TextFieldAfterTouchShowsKeyboard) {
  EXPECT_EQ(tu::decide({.editable = true, .osk_visible = false, .we_showed = false, .touch_recent = true}), action_e::show);
}

TEST(TouchUxDecide, TextFieldWithoutRecentTouchIsLeftAlone) {
  EXPECT_EQ(tu::decide({.editable = true, .osk_visible = false, .we_showed = false, .touch_recent = false}), action_e::none);
}

TEST(TouchUxDecide, VisibleKeyboardIsNotShownAgain) {
  EXPECT_EQ(tu::decide({.editable = true, .osk_visible = true, .we_showed = true, .touch_recent = true}), action_e::none);
}

TEST(TouchUxDecide, FocusLeavingTextFieldsHidesOnlyOurKeyboard) {
  EXPECT_EQ(tu::decide({.editable = false, .osk_visible = true, .we_showed = true, .touch_recent = true}), action_e::schedule_hide);
  EXPECT_EQ(tu::decide({.editable = false, .osk_visible = true, .we_showed = false, .touch_recent = true}), action_e::none);
}

TEST(TouchUxDecide, KeyboardClosedByUserIsForgotten) {
  EXPECT_EQ(tu::decide({.editable = false, .osk_visible = false, .we_showed = true, .touch_recent = false}), action_e::forget);
}

TEST(TouchUxRecent, WindowBoundaries) {
  const auto base = clock_t_::time_point {} + std::chrono::seconds {100};
  EXPECT_FALSE(tu::touch_is_recent(clock_t_::time_point {}, base));
  EXPECT_TRUE(tu::touch_is_recent(base, base));
  EXPECT_TRUE(tu::touch_is_recent(base, base + tu::touch_recent_window));
  EXPECT_FALSE(tu::touch_is_recent(base, base + tu::touch_recent_window + std::chrono::milliseconds {1}));
  EXPECT_FALSE(tu::touch_is_recent(base + std::chrono::seconds {1}, base));
}

TEST(TouchUxTaskbar, AutoHideBitIsAddedAndRestored) {
  constexpr unsigned int autohide = 0x1;
  constexpr unsigned int on_top = 0x2;
  EXPECT_EQ(tu::taskbar_state_for_touch(on_top, autohide), on_top | autohide);
  EXPECT_EQ(tu::taskbar_state_restored(on_top | autohide, autohide, false), on_top);
  EXPECT_EQ(tu::taskbar_state_restored(on_top, autohide, true), on_top | autohide);
}
