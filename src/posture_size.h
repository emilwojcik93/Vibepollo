/**
 * @file src/posture_size.h
 * @brief Pure client-posture parsing and virtual display physical size policy (contract v2).
 *
 * Touch-primary clients (phones, handhelds, tablets) get a virtual display that reports one
 * fixed physical diagonal, small enough for Windows to dock its touch keyboard. The size
 * depends only on the device type and keyboard/posture state: never on the client screen
 * size, DPI or aspect, and never on any physical monitor. The stream pixels only decide how
 * the fixed diagonal is split into width and height. Nothing here performs I/O or depends
 * on Windows or Boost.
 */
#pragma once

#include <cstdint>
#include <optional>
#include <string_view>

namespace posture {
  /// Client class declared by `clientPosture`.
  enum class client_class_e {
    desktop,
    laptop,
    tablet,
    handheld,
    phone,
  };

  /// Default host diagonal for touch-primary clients: 14 in. One value for phone, handheld and tablet.
  inline constexpr int default_touch_diagonal_mm = 356;

  // Accepted range of the host option `touch_posture_diagonal_mm`. Values outside are rejected,
  // never clamped (callers fall back to the default). The upper bound is 419 mm = 16.5 in. Windows
  // 11 build 26300 docks the touch keyboard up to 38x21 cm (43.4 cm, 17.1 in) and floats it from
  // 40x22 cm (45.7 cm, 18.0 in); the limit used here is 17.0 in (431.8 mm), a margin below the
  // largest size observed to dock. Rounding both sides to whole centimetres in the EDID (see
  // edid_size_cm) can add up to 7.8 mm; swept over every aspect from 1:64 to 64:1, a 419 mm
  // diagonal encodes at most 426.4 mm (16.8 in). The highest value that still stays below the
  // limit is 424 mm, so 419 keeps a 5 mm margin.
  inline constexpr int min_touch_diagonal_mm = 100;
  inline constexpr int max_touch_diagonal_mm = 419;

  /// Diagonal every EDID size must stay below so the Windows touch keyboard docks (observed, not documented): 17.0 in.
  inline constexpr double docking_limit_diagonal_mm = 17.0 * 25.4;

  // Per-side lower bound of the derived size. Inside what the virtual display driver accepts and
  // large enough to survive rounding to one whole centimetre in the EDID.
  inline constexpr int min_side_mm = 10;

  /// Lowercase wire name of a class.
  std::string_view to_string(client_class_e client_class);

  /// Parse `clientPosture`. Unknown values yield nullopt.
  std::optional<client_class_e> parse_posture(std::string_view value);

  /// Parse `clientTouch`. Only "0" and "1" are valid.
  std::optional<bool> parse_touch(std::string_view value);

  /// Parse the host option `touch_posture_diagonal_mm`: a decimal integer in [100, 419], digits only.
  std::optional<int> parse_touch_diagonal_mm(std::string_view value);

  /// The configured diagonal when it is inside [100, 419] mm, else the 356 mm default.
  int sanitize_touch_diagonal_mm(int configured_mm);

  /**
   * @brief Validated posture hints of one client.
   *
   * Every field is empty when the client sent nothing usable. `paired_touch` is the
   * host-side per-paired-client "touch device" flag and is not sent by clients.
   * `clientDiagonalMm` and `clientDpi` of older clients are deliberately not kept: they
   * have no influence on the size policy.
   */
  struct hints_t {
    std::optional<client_class_e> posture;
    std::optional<bool> touch;
    bool paired_touch = false;

    /// True when the client itself sent at least one valid argument.
    bool has_client_hints() const;

    friend bool operator==(const hints_t &, const hints_t &) = default;
  };

  /**
   * @brief Parse and validate the wire arguments that matter. Empty strings mean "absent".
   *
   * A non-empty `clientPosture` that is not a known class discards every hint, because
   * `clientTouch` cannot be interpreted without it. An invalid `clientTouch` is dropped.
   * The legacy `clientDiagonalMm` and `clientDpi` arguments are never read: hosts ignore
   * them, so older clients that still send them keep working.
   */
  hints_t parse_hints(std::string_view posture, std::string_view touch, bool paired_touch = false);

  /// Physical size of the virtual display in whole millimetres.
  struct physical_size_t {
    std::uint32_t width_mm;
    std::uint32_t height_mm;

    friend bool operator==(const physical_size_t &, const physical_size_t &) = default;
  };

  /**
   * @brief Class the size policy applies for these hints, or nullopt for "no policy".
   *
   * An explicit posture wins: tablet, handheld and phone apply, desktop and laptop do
   * not. Without a posture, `clientTouch=1` or the paired-client flag selects tablet.
   */
  std::optional<client_class_e> effective_class(const hints_t &hints);

  /**
   * @brief Derive the virtual display physical size (contract v2).
   *
   * One fixed diagonal for every touch-primary client: `diagonal_mm` when it is inside
   * [100, 419], else the 356 mm default. The stream pixels only split that diagonal into
   * width and height assuming square pixels (16:9 when a dimension is not positive), so
   * the result is the same for any stream resolution and any client screen size or aspect.
   * Each side is at least 10 mm. For every input the diagonal as encoded in the EDID
   * (whole centimetres, see edid_size_cm) stays below the 17.0 in docking limit.
   *
   * @return nullopt when no policy applies, so callers keep today's behaviour.
   */
  std::optional<physical_size_t> derive_physical_size(const hints_t &hints, int width_px, int height_px, int diagonal_mm = default_touch_diagonal_mm);

  /// Diagonal of a size in whole millimetres, for log lines and status text.
  std::uint32_t size_diagonal_mm(const physical_size_t &size);

  /**
   * @brief Whole centimetres the virtual display driver writes to EDID bytes 0x15/0x16.
   *
   * Mirrors libvirtualdisplay: round half up, clamped to [1, 255]. This is what Windows sees,
   * so it is the value the docking guarantee is checked against.
   */
  std::uint32_t edid_size_cm(std::uint32_t millimetres);
}  // namespace posture
