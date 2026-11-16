/**
 * @file src/posture_size.h
 * @brief Pure client-posture parsing and virtual display physical size policy.
 *
 * Touch clients (phones, handhelds, tablets) get a virtual display whose reported
 * physical size is small enough for Windows to dock its touch keyboard. The size is
 * derived from client supplied hints or a class default; it never depends on any
 * physical monitor. Nothing here performs I/O or depends on Windows or Boost.
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

  // Accepted ranges of the wire arguments. Values outside are ignored, never clamped.
  inline constexpr int min_diagonal_arg_mm = 50;
  inline constexpr int max_diagonal_arg_mm = 800;
  inline constexpr int min_dpi_arg = 72;
  inline constexpr int max_dpi_arg = 800;

  // Class default diagonals, used when the client sends neither a diagonal nor a DPI.
  inline constexpr int phone_default_diagonal_mm = 165;  ///< 6.5 in
  inline constexpr int handheld_default_diagonal_mm = 203;  ///< 8 in
  inline constexpr int tablet_default_diagonal_mm = 305;  ///< 12 in

  // Every derived diagonal is clamped to this range. The upper bound is 16.5 in, a margin
  // below the 18 in size at which Windows stops docking its touch keyboard.
  inline constexpr int min_diagonal_mm = 100;
  inline constexpr int max_diagonal_mm = 419;

  // Per-side bounds of the derived size. Inside what the virtual display driver accepts.
  inline constexpr int min_side_mm = 10;
  inline constexpr int max_side_mm = 419;

  /// Lowercase wire name of a class.
  std::string_view to_string(client_class_e client_class);

  /// Parse `clientPosture`. Unknown values yield nullopt.
  std::optional<client_class_e> parse_posture(std::string_view value);

  /// Parse `clientTouch`. Only "0" and "1" are valid.
  std::optional<bool> parse_touch(std::string_view value);

  /// Parse `clientDiagonalMm`: a decimal integer in [50, 800].
  std::optional<int> parse_diagonal_mm(std::string_view value);

  /// Parse `clientDpi`: a decimal integer in [72, 800].
  std::optional<int> parse_dpi(std::string_view value);

  /**
   * @brief Validated posture hints of one client.
   *
   * Every field is empty when the client sent nothing usable. `paired_touch` is the
   * host-side per-paired-client "touch device" flag and is not sent by clients.
   */
  struct hints_t {
    std::optional<client_class_e> posture;
    std::optional<bool> touch;
    std::optional<int> diagonal_mm;
    std::optional<int> dpi;
    bool paired_touch = false;

    /// True when the client itself sent at least one valid argument.
    bool has_client_hints() const;

    friend bool operator==(const hints_t &, const hints_t &) = default;
  };

  /**
   * @brief Parse and validate the four wire arguments. Empty strings mean "absent".
   *
   * A non-empty `clientPosture` that is not a known class discards every hint, because
   * the remaining values cannot be interpreted without it. Other invalid values are
   * dropped individually.
   */
  hints_t parse_hints(
    std::string_view posture,
    std::string_view touch,
    std::string_view diagonal_mm,
    std::string_view dpi,
    bool paired_touch = false
  );

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
   * @brief Derive the virtual display physical size.
   *
   * Diagonal is the client diagonal if present, else the stream pixel diagonal divided
   * by the DPI, else the class default, then clamped to [100, 419] mm and split by the
   * stream aspect ratio (16:9 when a dimension is not positive).
   *
   * @return nullopt when no policy applies, so callers keep today's behaviour.
   */
  std::optional<physical_size_t> derive_physical_size(const hints_t &hints, int width_px, int height_px);
}  // namespace posture
