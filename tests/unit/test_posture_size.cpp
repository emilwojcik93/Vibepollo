#include "../tests_common.h"

#include "src/posture_size.h"

#include <cmath>
#include <cstdint>

namespace {
  using namespace posture;

  hints_t hints_for(client_class_e client_class) {
    hints_t hints;
    hints.posture = client_class;
    return hints;
  }

  double diagonal_of(const physical_size_t &size) {
    return std::hypot(static_cast<double>(size.width_mm), static_cast<double>(size.height_mm));
  }
}  // namespace

TEST(PostureParse, PostureAcceptsOnlyKnownLowercaseClasses) {
  EXPECT_EQ(parse_posture("desktop"), client_class_e::desktop);
  EXPECT_EQ(parse_posture("laptop"), client_class_e::laptop);
  EXPECT_EQ(parse_posture("tablet"), client_class_e::tablet);
  EXPECT_EQ(parse_posture("handheld"), client_class_e::handheld);
  EXPECT_EQ(parse_posture("phone"), client_class_e::phone);
  EXPECT_FALSE(parse_posture("Tablet"));
  EXPECT_FALSE(parse_posture("tv"));
  EXPECT_FALSE(parse_posture(""));
}

TEST(PostureParse, TouchAcceptsOnlyZeroAndOne) {
  EXPECT_EQ(parse_touch("1"), true);
  EXPECT_EQ(parse_touch("0"), false);
  EXPECT_FALSE(parse_touch("2"));
  EXPECT_FALSE(parse_touch("true"));
  EXPECT_FALSE(parse_touch(""));
}

TEST(PostureParse, DiagonalRangeIsInclusiveAndStrict) {
  EXPECT_FALSE(parse_diagonal_mm("49"));
  EXPECT_EQ(parse_diagonal_mm("50"), 50);
  EXPECT_EQ(parse_diagonal_mm("800"), 800);
  EXPECT_FALSE(parse_diagonal_mm("801"));
  EXPECT_FALSE(parse_diagonal_mm("-300"));
  EXPECT_FALSE(parse_diagonal_mm("+300"));
  EXPECT_FALSE(parse_diagonal_mm("300.5"));
  EXPECT_FALSE(parse_diagonal_mm(" 300"));
  EXPECT_FALSE(parse_diagonal_mm("300mm"));
  EXPECT_FALSE(parse_diagonal_mm("99999999999999999999"));
  EXPECT_FALSE(parse_diagonal_mm(""));
}

TEST(PostureParse, DpiRangeIsInclusiveAndStrict) {
  EXPECT_FALSE(parse_dpi("71"));
  EXPECT_EQ(parse_dpi("72"), 72);
  EXPECT_EQ(parse_dpi("420"), 420);
  EXPECT_EQ(parse_dpi("800"), 800);
  EXPECT_FALSE(parse_dpi("801"));
  EXPECT_FALSE(parse_dpi("0"));
  EXPECT_FALSE(parse_dpi("abc"));
  EXPECT_FALSE(parse_dpi(""));
}

TEST(PostureParse, HintsKeepValidFieldsAndDropInvalidOnes) {
  const auto hints = parse_hints("phone", "1", "100000", "420");
  EXPECT_EQ(hints.posture, client_class_e::phone);
  EXPECT_EQ(hints.touch, true);
  EXPECT_FALSE(hints.diagonal_mm);
  EXPECT_EQ(hints.dpi, 420);
  EXPECT_TRUE(hints.has_client_hints());
}

TEST(PostureParse, UnknownPostureDiscardsEveryHint) {
  const auto hints = parse_hints("hologram", "1", "300", "420", true);
  EXPECT_FALSE(hints.posture);
  EXPECT_FALSE(hints.touch);
  EXPECT_FALSE(hints.diagonal_mm);
  EXPECT_FALSE(hints.dpi);
  EXPECT_FALSE(hints.has_client_hints());
  // The host-side flag is not a client hint and survives.
  EXPECT_TRUE(hints.paired_touch);
}

TEST(PostureParse, NothingSentMeansNoHints) {
  const auto hints = parse_hints("", "", "", "");
  EXPECT_FALSE(hints.has_client_hints());
  EXPECT_FALSE(hints.paired_touch);
  EXPECT_EQ(hints, hints_t {});
}

TEST(PostureClass, TouchClassesApplyAndNonTouchClassesDoNot) {
  EXPECT_EQ(effective_class(hints_for(client_class_e::phone)), client_class_e::phone);
  EXPECT_EQ(effective_class(hints_for(client_class_e::handheld)), client_class_e::handheld);
  EXPECT_EQ(effective_class(hints_for(client_class_e::tablet)), client_class_e::tablet);
  EXPECT_FALSE(effective_class(hints_for(client_class_e::laptop)));
  EXPECT_FALSE(effective_class(hints_for(client_class_e::desktop)));
}

TEST(PostureClass, TouchWithoutPostureIsTablet) {
  EXPECT_EQ(effective_class(parse_hints("", "1", "", "")), client_class_e::tablet);
  EXPECT_FALSE(effective_class(parse_hints("", "0", "", "")));
  EXPECT_FALSE(effective_class(hints_t {}));
}

TEST(PostureClass, ExplicitPostureBeatsTouchAndPairedFlag) {
  EXPECT_FALSE(effective_class(parse_hints("laptop", "1", "", "", true)));
  EXPECT_FALSE(effective_class(parse_hints("desktop", "", "", "", true)));
  EXPECT_EQ(effective_class(parse_hints("phone", "", "", "", true)), client_class_e::phone);
}

TEST(PostureClass, PairedTouchFlagAppliesTabletWhenNoPostureIsSent) {
  EXPECT_EQ(effective_class(parse_hints("", "", "", "", true)), client_class_e::tablet);
  // clientTouch=0 is informational and does not cancel the host flag.
  EXPECT_EQ(effective_class(parse_hints("", "0", "", "", true)), client_class_e::tablet);
  // An unusable posture is ignored, so the host flag still applies.
  EXPECT_EQ(effective_class(parse_hints("hologram", "", "", "", true)), client_class_e::tablet);
}

TEST(PostureSize, NoHintMeansNoSize) {
  EXPECT_FALSE(derive_physical_size(hints_t {}, 1920, 1080));
  EXPECT_FALSE(derive_physical_size(parse_hints("", "", "300", "420"), 1920, 1080));
  EXPECT_FALSE(derive_physical_size(hints_for(client_class_e::laptop), 1920, 1080));
}

TEST(PostureSize, ClassDefaultsOn16x9) {
  // 165, 203 and 305 mm diagonals split 16:9.
  EXPECT_EQ(derive_physical_size(hints_for(client_class_e::phone), 1920, 1080), (physical_size_t {144, 81}));
  EXPECT_EQ(derive_physical_size(hints_for(client_class_e::handheld), 1920, 1080), (physical_size_t {177, 100}));
  EXPECT_EQ(derive_physical_size(hints_for(client_class_e::tablet), 1920, 1080), (physical_size_t {266, 150}));
}

TEST(PostureSize, ClassDefaultDiagonalsMatchTheContract) {
  EXPECT_EQ(phone_default_diagonal_mm, 165);
  EXPECT_EQ(handheld_default_diagonal_mm, 203);
  EXPECT_EQ(tablet_default_diagonal_mm, 305);
  EXPECT_EQ(min_diagonal_mm, 100);
  EXPECT_EQ(max_diagonal_mm, 419);
}

TEST(PostureSize, PhoneWithDpiIsUsedAsIsBelowTheClamp) {
  // 2400x1080 at 420 dpi: 2631.8 px / 420 * 25.4 = 159.2 mm.
  const auto size = derive_physical_size(parse_hints("phone", "", "", "420"), 2400, 1080);
  ASSERT_TRUE(size);
  EXPECT_EQ(*size, (physical_size_t {145, 65}));
  EXPECT_NEAR(diagonal_of(*size), 159.2, 1.0);
}

TEST(PostureSize, ClientDiagonalBeatsDpi) {
  const auto size = derive_physical_size(parse_hints("tablet", "", "250", "420"), 1920, 1080);
  ASSERT_TRUE(size);
  EXPECT_NEAR(diagonal_of(*size), 250.0, 1.0);
}

TEST(PostureSize, DpiWithoutStreamPixelsFallsBackToClassDefault) {
  EXPECT_EQ(derive_physical_size(parse_hints("phone", "", "", "420"), 0, 0), (physical_size_t {144, 81}));
}

TEST(PostureSize, LargeTouchTabletIsClampedKeepingAspect) {
  // 22 in = 559 mm.
  const auto size = derive_physical_size(parse_hints("tablet", "", "559", ""), 1920, 1080);
  ASSERT_TRUE(size);
  EXPECT_EQ(*size, (physical_size_t {365, 205}));
  EXPECT_LE(diagonal_of(*size), max_diagonal_mm + 1.0);
  EXPECT_NEAR(static_cast<double>(size->width_mm) / size->height_mm, 16.0 / 9.0, 0.02);
}

TEST(PostureSize, HugeDpiDerivedDiagonalIsClamped) {
  // 3840x2160 at 72 dpi is far above 16.5 in.
  const auto size = derive_physical_size(parse_hints("tablet", "", "", "72"), 3840, 2160);
  ASSERT_TRUE(size);
  EXPECT_LE(diagonal_of(*size), max_diagonal_mm + 1.0);
  EXPECT_GE(diagonal_of(*size), max_diagonal_mm - 1.0);
}

TEST(PostureSize, TinyDiagonalIsRaisedToTheMinimum) {
  const auto size = derive_physical_size(parse_hints("phone", "", "50", ""), 1920, 1080);
  ASSERT_TRUE(size);
  EXPECT_NEAR(diagonal_of(*size), min_diagonal_mm, 1.0);
}

TEST(PostureSize, AspectRatioFollowsTheStreamNotTheClientScreen) {
  const auto portrait = derive_physical_size(hints_for(client_class_e::phone), 1080, 2400);
  const auto ultrawide = derive_physical_size(hints_for(client_class_e::tablet), 3440, 1440);
  ASSERT_TRUE(portrait);
  ASSERT_TRUE(ultrawide);
  EXPECT_LT(portrait->width_mm, portrait->height_mm);
  EXPECT_GT(ultrawide->width_mm, 2 * ultrawide->height_mm);
}

TEST(PostureSize, UnknownStreamSizeAssumes16x9) {
  EXPECT_EQ(derive_physical_size(hints_for(client_class_e::tablet), 0, 0), derive_physical_size(hints_for(client_class_e::tablet), 1920, 1080));
  EXPECT_EQ(derive_physical_size(hints_for(client_class_e::tablet), -1, 1080), derive_physical_size(hints_for(client_class_e::tablet), 1920, 1080));
}

TEST(PostureSize, ExtremeAspectKeepsEverySideInsideTheDriverRange) {
  const auto size = derive_physical_size(hints_for(client_class_e::tablet), 7680, 200);
  ASSERT_TRUE(size);
  EXPECT_GE(size->width_mm, static_cast<std::uint32_t>(min_side_mm));
  EXPECT_GE(size->height_mm, static_cast<std::uint32_t>(min_side_mm));
  EXPECT_LE(size->width_mm, static_cast<std::uint32_t>(max_side_mm));
  EXPECT_LE(size->height_mm, static_cast<std::uint32_t>(max_side_mm));
}

TEST(PostureSize, PairedTouchFlagUsesTabletDefaults) {
  EXPECT_EQ(derive_physical_size(parse_hints("", "", "", "", true), 1920, 1080), derive_physical_size(hints_for(client_class_e::tablet), 1920, 1080));
}

TEST(PostureSize, SizeIsDeterministic) {
  const auto hints = parse_hints("handheld", "1", "", "300");
  EXPECT_EQ(derive_physical_size(hints, 1920, 1200), derive_physical_size(hints, 1920, 1200));
}
