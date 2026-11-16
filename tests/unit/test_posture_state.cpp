#include "../tests_common.h"

#include "src/posture_state.h"

#include <chrono>

namespace {
  using namespace posture;
  using status_e = update_result_t::status_e;

  display_key_t key_for(std::uint8_t id) {
    display_key_t key {};
    key.fill(id);
    return key;
  }

  const auto t0 = std::chrono::steady_clock::time_point {} + std::chrono::hours {1};
  const physical_size_t tablet_16x9 {266, 150};
}  // namespace

TEST(PostureDisplayHandoff, NothingRecordedMeansDriverDefault) {
  forget_all_displays();
  EXPECT_FALSE(desired_size(key_for(1)));
  EXPECT_FALSE(applied_size(key_for(1)));
}

TEST(PostureDisplayHandoff, DesiredSizeIsOverwrittenPerCreate) {
  forget_all_displays();
  set_desired_size(key_for(2), tablet_16x9);
  EXPECT_EQ(desired_size(key_for(2)), tablet_16x9);
  set_desired_size(key_for(2), std::nullopt);
  EXPECT_FALSE(desired_size(key_for(2)));
}

TEST(PostureDisplayHandoff, AppliedStateDistinguishesUnknownFromDriverDefault) {
  forget_all_displays();
  note_applied_size(key_for(3), std::nullopt);
  const auto applied = applied_size(key_for(3));
  ASSERT_TRUE(applied.has_value());
  EXPECT_FALSE(applied->has_value());

  note_applied_size(key_for(3), tablet_16x9);
  ASSERT_TRUE(applied_size(key_for(3)).has_value());
  EXPECT_EQ(*applied_size(key_for(3)), std::optional<physical_size_t> {tablet_16x9});
}

TEST(PostureDisplayHandoff, ForgetDropsDesiredAndApplied) {
  forget_all_displays();
  set_desired_size(key_for(4), tablet_16x9);
  note_applied_size(key_for(4), tablet_16x9);
  forget_display(key_for(4));
  EXPECT_FALSE(desired_size(key_for(4)));
  EXPECT_FALSE(applied_size(key_for(4)));
}

TEST(PostureBoundary, UnknownAppliedStateNeverCrosses) {
  EXPECT_FALSE(display_crosses_boundary(std::nullopt, parse_hints("tablet", "", "", "")));
  EXPECT_FALSE(display_crosses_boundary(std::nullopt, hints_t {}));
}

TEST(PostureBoundary, OnlyNoneVersusSomeCrosses) {
  const std::optional<std::optional<physical_size_t>> created_without_size {std::optional<physical_size_t> {}};
  const std::optional<std::optional<physical_size_t>> created_with_size {std::optional<physical_size_t> {tablet_16x9}};

  EXPECT_TRUE(display_crosses_boundary(created_without_size, parse_hints("phone", "", "", "")));
  EXPECT_FALSE(display_crosses_boundary(created_without_size, parse_hints("laptop", "", "", "")));
  EXPECT_FALSE(display_crosses_boundary(created_without_size, hints_t {}));

  EXPECT_TRUE(display_crosses_boundary(created_with_size, parse_hints("laptop", "", "", "")));
  EXPECT_TRUE(display_crosses_boundary(created_with_size, hints_t {}));
  // Phone to tablet keeps the display even though the size differs.
  EXPECT_FALSE(display_crosses_boundary(created_with_size, parse_hints("phone", "", "", "")));
  EXPECT_FALSE(display_crosses_boundary(created_with_size, parse_hints("tablet", "", "400", "")));
}

TEST(PostureRuntimeUpdate, UnknownClientHasNoSession) {
  const auto result = apply_runtime_update("state-test-unknown", parse_hints("tablet", "", "", ""), t0);
  EXPECT_EQ(result.status, status_e::no_session);
  EXPECT_FALSE(status_for_client("state-test-unknown"));
}

TEST(PostureRuntimeUpdate, FlipToTabletCrossesTheBoundary) {
  record_launch("state-test-flip", parse_hints("laptop", "", "", ""), 1920, 1080);
  const auto result = apply_runtime_update("state-test-flip", parse_hints("tablet", "1", "", ""), t0);
  EXPECT_EQ(result.status, status_e::changed);
  EXPECT_TRUE(result.boundary_crossed);
  EXPECT_FALSE(result.previous);
  EXPECT_EQ(result.current, std::optional<physical_size_t> {tablet_16x9});

  const auto status = status_for_client("state-test-flip");
  ASSERT_TRUE(status);
  EXPECT_EQ(status->client_class, client_class_e::tablet);
  EXPECT_EQ(status->wanted_size, std::optional<physical_size_t> {tablet_16x9});
  EXPECT_TRUE(status->update_pending);
}

TEST(PostureRuntimeUpdate, PhoneToTabletChangesSizeWithoutCrossing) {
  record_launch("state-test-phone", parse_hints("phone", "", "", ""), 1920, 1080);
  const auto result = apply_runtime_update("state-test-phone", parse_hints("tablet", "", "", ""), t0);
  EXPECT_EQ(result.status, status_e::changed);
  EXPECT_FALSE(result.boundary_crossed);
}

TEST(PostureRuntimeUpdate, SameStateIsUnchangedAndNotPending) {
  record_launch("state-test-same", parse_hints("tablet", "", "", ""), 1920, 1080);
  const auto result = apply_runtime_update("state-test-same", parse_hints("tablet", "1", "", ""), t0);
  EXPECT_EQ(result.status, status_e::unchanged);
  EXPECT_FALSE(result.boundary_crossed);
  EXPECT_FALSE(status_for_client("state-test-same")->update_pending);
}

TEST(PostureRuntimeUpdate, CallsCloserThanOneSecondAreIgnored) {
  record_launch("state-test-rate", parse_hints("laptop", "", "", ""), 1920, 1080);
  EXPECT_EQ(apply_runtime_update("state-test-rate", parse_hints("tablet", "", "", ""), t0).status, status_e::changed);
  EXPECT_EQ(apply_runtime_update("state-test-rate", parse_hints("laptop", "", "", ""), t0 + std::chrono::milliseconds {999}).status, status_e::rate_limited);
  // The ignored call changed nothing.
  EXPECT_EQ(status_for_client("state-test-rate")->client_class, client_class_e::tablet);
  EXPECT_EQ(apply_runtime_update("state-test-rate", parse_hints("laptop", "", "", ""), t0 + std::chrono::milliseconds {1000}).status, status_e::changed);
}

TEST(PostureRuntimeUpdate, RateLimitIsPerClient) {
  record_launch("state-test-rate-a", parse_hints("laptop", "", "", ""), 1920, 1080);
  record_launch("state-test-rate-b", parse_hints("laptop", "", "", ""), 1920, 1080);
  EXPECT_EQ(apply_runtime_update("state-test-rate-a", parse_hints("tablet", "", "", ""), t0).status, status_e::changed);
  EXPECT_EQ(apply_runtime_update("state-test-rate-b", parse_hints("tablet", "", "", ""), t0).status, status_e::changed);
}

TEST(PostureRuntimeUpdate, NewLaunchClearsPending) {
  record_launch("state-test-relaunch", parse_hints("laptop", "", "", ""), 1920, 1080);
  apply_runtime_update("state-test-relaunch", parse_hints("tablet", "", "", ""), t0);
  record_launch("state-test-relaunch", parse_hints("tablet", "", "", ""), 1920, 1080);
  EXPECT_FALSE(status_for_client("state-test-relaunch")->update_pending);
}

TEST(PostureStatus, ReportsTheDisplaySizeOwnedByTheClient) {
  forget_all_displays();
  record_launch("state-test-status", parse_hints("", "", "", "", true), 1920, 1080);
  set_desired_size(key_for(9), tablet_16x9, "state-test-status");
  note_applied_size(key_for(9), tablet_16x9);

  const auto status = status_for_client("state-test-status");
  ASSERT_TRUE(status);
  EXPECT_TRUE(status->paired_touch);
  EXPECT_FALSE(status->posture);
  EXPECT_EQ(status->client_class, client_class_e::tablet);
  EXPECT_TRUE(status->applied_known);
  EXPECT_EQ(status->applied_size, std::optional<physical_size_t> {tablet_16x9});
  EXPECT_FALSE(status->update_pending);
}

TEST(PostureStatus, AppliedStateIsUnknownWithoutAnOwnedDisplay) {
  forget_all_displays();
  record_launch("state-test-nodisplay", hints_t {}, 1920, 1080);
  const auto status = status_for_client("state-test-nodisplay");
  ASSERT_TRUE(status);
  EXPECT_FALSE(status->applied_known);
  EXPECT_FALSE(status->client_class);
}
