/**
 * @file src/posture_size.cpp
 * @brief Pure client-posture parsing and virtual display physical size policy.
 */
#include "posture_size.h"

#include <algorithm>
#include <cmath>

namespace posture {
  namespace {
    constexpr double mm_per_inch = 25.4;

    // Strict decimal integer: digits only, bounded length, inside [min, max].
    std::optional<int> parse_ranged_int(std::string_view value, int min, int max) {
      if (value.empty() || value.size() > 6) {
        return std::nullopt;
      }
      int parsed = 0;
      for (const char c : value) {
        if (c < '0' || c > '9') {
          return std::nullopt;
        }
        parsed = parsed * 10 + (c - '0');
      }
      if (parsed < min || parsed > max) {
        return std::nullopt;
      }
      return parsed;
    }

    double class_default_diagonal_mm(client_class_e client_class) {
      switch (client_class) {
        case client_class_e::phone:
          return phone_default_diagonal_mm;
        case client_class_e::handheld:
          return handheld_default_diagonal_mm;
        default:
          return tablet_default_diagonal_mm;
      }
    }
  }  // namespace

  std::string_view to_string(client_class_e client_class) {
    switch (client_class) {
      case client_class_e::desktop:
        return "desktop";
      case client_class_e::laptop:
        return "laptop";
      case client_class_e::tablet:
        return "tablet";
      case client_class_e::handheld:
        return "handheld";
      case client_class_e::phone:
        return "phone";
    }
    return "desktop";
  }

  std::optional<client_class_e> parse_posture(std::string_view value) {
    for (const auto client_class : {client_class_e::desktop, client_class_e::laptop, client_class_e::tablet, client_class_e::handheld, client_class_e::phone}) {
      if (value == to_string(client_class)) {
        return client_class;
      }
    }
    return std::nullopt;
  }

  std::optional<bool> parse_touch(std::string_view value) {
    if (value == "1") {
      return true;
    }
    if (value == "0") {
      return false;
    }
    return std::nullopt;
  }

  std::optional<int> parse_diagonal_mm(std::string_view value) {
    return parse_ranged_int(value, min_diagonal_arg_mm, max_diagonal_arg_mm);
  }

  std::optional<int> parse_dpi(std::string_view value) {
    return parse_ranged_int(value, min_dpi_arg, max_dpi_arg);
  }

  bool hints_t::has_client_hints() const {
    return posture || touch || diagonal_mm || dpi;
  }

  hints_t parse_hints(
    std::string_view posture_value,
    std::string_view touch_value,
    std::string_view diagonal_mm_value,
    std::string_view dpi_value,
    bool paired_touch
  ) {
    hints_t hints;
    hints.paired_touch = paired_touch;

    if (!posture_value.empty()) {
      hints.posture = parse_posture(posture_value);
      if (!hints.posture) {
        return hints;
      }
    }
    hints.touch = parse_touch(touch_value);
    hints.diagonal_mm = parse_diagonal_mm(diagonal_mm_value);
    hints.dpi = parse_dpi(dpi_value);
    return hints;
  }

  std::optional<client_class_e> effective_class(const hints_t &hints) {
    if (hints.posture) {
      switch (*hints.posture) {
        case client_class_e::tablet:
        case client_class_e::handheld:
        case client_class_e::phone:
          return hints.posture;
        default:
          return std::nullopt;
      }
    }
    if ((hints.touch && *hints.touch) || hints.paired_touch) {
      return client_class_e::tablet;
    }
    return std::nullopt;
  }

  std::optional<physical_size_t> derive_physical_size(const hints_t &hints, int width_px, int height_px) {
    const auto client_class = effective_class(hints);
    if (!client_class) {
      return std::nullopt;
    }

    const bool have_pixels = width_px > 0 && height_px > 0;
    const double pixel_diagonal = have_pixels ? std::hypot(static_cast<double>(width_px), static_cast<double>(height_px)) : 0.0;

    double diagonal_mm = class_default_diagonal_mm(*client_class);
    if (hints.diagonal_mm) {
      diagonal_mm = *hints.diagonal_mm;
    } else if (hints.dpi && have_pixels) {
      diagonal_mm = pixel_diagonal / *hints.dpi * mm_per_inch;
    }
    diagonal_mm = std::clamp(diagonal_mm, static_cast<double>(min_diagonal_mm), static_cast<double>(max_diagonal_mm));

    // 16:9 when the stream size is unknown.
    const double aspect_width = have_pixels ? width_px : 16;
    const double aspect_height = have_pixels ? height_px : 9;
    const double aspect_diagonal = have_pixels ? pixel_diagonal : std::hypot(aspect_width, aspect_height);

    const auto side_mm = [&](double aspect_side) {
      const double side = std::round(diagonal_mm * aspect_side / aspect_diagonal);
      return static_cast<std::uint32_t>(std::clamp(side, static_cast<double>(min_side_mm), static_cast<double>(max_side_mm)));
    };
    return physical_size_t {side_mm(aspect_width), side_mm(aspect_height)};
  }
}  // namespace posture
