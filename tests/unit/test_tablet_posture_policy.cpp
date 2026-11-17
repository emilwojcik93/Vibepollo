#include "../tests_common.h"
#include "src/tablet_posture_policy.h"

namespace {
  using namespace tablet_posture;
}

TEST(TabletPosturePolicy, StartsAsLaptopWithoutClients) {
  registry_t registry;
  EXPECT_EQ(registry.state(), state_e::laptop);
  EXPECT_EQ(registry.size(), 0u);
}

TEST(TabletPosturePolicy, TouchClientMakesTheHostASlate) {
  registry_t registry;
  EXPECT_TRUE(registry.start("a55", true));
  EXPECT_EQ(registry.state(), state_e::slate);
}

TEST(TabletPosturePolicy, LaptopClientKeepsDesktop) {
  registry_t registry;
  EXPECT_FALSE(registry.start("dell", false));
  EXPECT_EQ(registry.state(), state_e::laptop);
}

TEST(TabletPosturePolicy, KeyboardFlipFollowsRuntimeUpdates) {
  registry_t registry;
  registry.start("dell", false);
  EXPECT_TRUE(registry.update("dell", true));  // keyboard folded back
  EXPECT_EQ(registry.state(), state_e::slate);
  EXPECT_FALSE(registry.update("dell", true));  // same class again: no change
  EXPECT_TRUE(registry.update("dell", false));  // keyboard back
  EXPECT_EQ(registry.state(), state_e::laptop);
}

TEST(TabletPosturePolicy, UpdateOfUnknownClientIsIgnored) {
  registry_t registry;
  EXPECT_FALSE(registry.update("ghost", true));
  EXPECT_EQ(registry.state(), state_e::laptop);
  EXPECT_FALSE(registry.contains("ghost"));
}

TEST(TabletPosturePolicy, SlateWhileAnyClientIsTouchPrimary) {
  registry_t registry;
  registry.start("a55", true);
  EXPECT_FALSE(registry.start("dell", false));
  EXPECT_EQ(registry.state(), state_e::slate);
  EXPECT_FALSE(registry.stop("dell"));
  EXPECT_EQ(registry.state(), state_e::slate);
  EXPECT_TRUE(registry.stop("a55"));
  EXPECT_EQ(registry.state(), state_e::laptop);
  EXPECT_EQ(registry.size(), 0u);
}

TEST(TabletPosturePolicy, ClientWithSeveralSessionsStaysUntilTheLastEnds) {
  registry_t registry;
  registry.start("a55", true);
  registry.start("a55", true);
  EXPECT_FALSE(registry.stop("a55"));
  EXPECT_EQ(registry.state(), state_e::slate);
  EXPECT_TRUE(registry.stop("a55"));
  EXPECT_EQ(registry.state(), state_e::laptop);
}

TEST(TabletPosturePolicy, StoppingAnUnknownClientIsIgnored) {
  registry_t registry;
  EXPECT_FALSE(registry.stop("ghost"));
  EXPECT_EQ(registry.state(), state_e::laptop);
}

TEST(TabletPosturePolicy, NewSessionTakesTheLatestPosture) {
  registry_t registry;
  registry.start("dell", true);
  EXPECT_TRUE(registry.start("dell", false));  // resumed with the keyboard attached
  EXPECT_EQ(registry.state(), state_e::laptop);
}

TEST(TabletPosturePolicy, OverrideBeatsTheClients) {
  registry_t registry;
  registry.start("dell", false);
  registry.set_override(state_e::slate);  // simulated keyboard detach
  EXPECT_EQ(registry.state(), state_e::slate);
  EXPECT_EQ(registry.client_state(), state_e::laptop);
  registry.set_override(state_e::laptop);
  registry.update("dell", true);
  EXPECT_EQ(registry.state(), state_e::laptop);
  EXPECT_EQ(registry.client_state(), state_e::slate);
  registry.set_override(std::nullopt);  // back to client control
  EXPECT_EQ(registry.state(), state_e::slate);
}

TEST(TabletPosturePolicy, OverrideWorksWithoutAnyClient) {
  registry_t registry;
  registry.set_override(state_e::slate);
  EXPECT_EQ(registry.state(), state_e::slate);
  EXPECT_EQ(registry.size(), 0u);
  EXPECT_EQ(registry.override_state(), std::optional<state_e> {state_e::slate});
}

TEST(TabletPosturePolicy, ClientsReportEveryActiveSession) {
  registry_t registry;
  registry.start("a55", true);
  registry.start("a55", true);
  registry.start("dell", false);
  const auto clients = registry.clients();
  ASSERT_EQ(clients.size(), 2u);
  EXPECT_EQ(clients[0].id, "a55");
  EXPECT_EQ(clients[0].sessions, 2);
  EXPECT_TRUE(clients[0].touch_primary);
  EXPECT_EQ(clients[1].id, "dell");
  EXPECT_FALSE(clients[1].touch_primary);
}
