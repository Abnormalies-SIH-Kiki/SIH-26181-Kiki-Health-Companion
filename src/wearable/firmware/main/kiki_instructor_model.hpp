#pragma once

// Platform-independent 3D rig and software renderer. Also used by the desktop
// preview/kinematics test, so acceptance images use the watch's exact geometry.
#include <cstdint>

namespace kiki::instructor {
constexpr int kWidth = 320;
constexpr int kHeight = 306;
enum class Move { ArmRaise, LateralRaise, ElbowCurl, ShoulderRoll, SeatedMarch, TorsoTwist };
enum class Side { Both, Left, Right };
struct Vec { float x, y, z; };
struct Pose {
    Vec hip[2], knee[2], ankle[2], shoulder[2], elbow[2], wrist[2];
    float twist;
};
struct Command {
    Move move = Move::ArmRaise;
    Side side = Side::Both;
    bool hold = false;
    float period = 6;
};
bool parse_move(const char *name, Move &out);
const char *move_title(Move move);
Pose pose(const Command &command, float seconds);
// RGB565 colour and float depth buffers must each contain kWidth*kHeight entries.
void render(uint16_t *pixels, float *depth, const Command &command, float seconds);
}  // namespace kiki::instructor
