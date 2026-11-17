#pragma once
#include <iostream>
#include <string_view>
// Mirrors the real logging.h closely enough that a local variable hiding a logger name fails to compile.
namespace probe_log { struct logger_t {}; inline std::ostream &pick(logger_t &) { return std::cerr; } }
inline probe_log::logger_t verbose, debug, info, warning, error, fatal;
#define BOOST_LOG(lvl) probe_log::pick(lvl)
