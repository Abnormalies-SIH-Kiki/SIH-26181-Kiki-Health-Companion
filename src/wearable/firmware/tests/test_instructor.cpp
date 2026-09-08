// Host kinematics checks and optional exact-renderer preview frames.
// g++ -O2 -std=c++17 -I firmware/main firmware/tests/test_instructor.cpp
//     firmware/main/kiki_instructor_model.cpp -o /tmp/test_instructor
// /tmp/test_instructor [existing-preview-directory]
#include "kiki_instructor_model.hpp"
#include <cassert>
#include <cmath>
#include <cstdio>
#include <string>
#include <vector>
using namespace kiki::instructor;
float distance(Vec a,Vec b) {
    return std::sqrt((a.x-b.x)*(a.x-b.x)+(a.y-b.y)*(a.y-b.y)+(a.z-b.z)*(a.z-b.z));
}
int main(int argc,char **argv) {
    const char *moves[]={"arm_raise","lateral_raise","elbow_curl","shoulder_roll","seated_march","torso_twist"};
    for(const char *move:moves) {
        Command c;
        assert(parse_move(move,c.move));
        for(Side side:{Side::Both,Side::Left,Side::Right}) for(bool hold:{false,true}) {
            c.side=side;c.hold=hold;
            const Pose neutral=pose(c,-1);
            for(int frame=0;frame<240;++frame) {
                Pose p=pose(c,frame*.1f);
                for(int i=0;i<2;++i) {
                    assert(std::abs(distance(p.shoulder[i],p.elbow[i])-.296f)<.004f);
                    assert(std::abs(distance(p.elbow[i],p.wrist[i])-.267f)<.005f);
                    assert(std::abs(distance(p.hip[i],p.knee[i])-.43f)<.002f);
                    assert(std::abs(distance(p.knee[i],p.ankle[i])-.43f)<.001f);
                    assert(p.ankle[i].y>=.099f); // feet never pass through the floor
                    assert(distance(p.hip[i],neutral.hip[i])<.001f);
                    if(side!=Side::Both && c.move!=Move::TorsoTwist &&
                       ((i==0)!=(side==Side::Left))) {
                        assert(distance(p.wrist[i],neutral.wrist[i])<.001f);
                        assert(distance(p.ankle[i],neutral.ankle[i])<.001f);
                    }
                }
            }
            if(hold) assert(distance(pose(c,3).wrist[0],pose(c,20).wrist[0])<.001f);
        }
        if(argc>1) {
            c.side=Side::Both;c.hold=false;
            std::vector<uint16_t> pixels(kWidth*kHeight);
            std::vector<float> depth(kWidth*kHeight);
            for(int frame=0;frame<60;++frame) {
                render(pixels.data(),depth.data(),c,frame*.2f);
                char suffix[128];std::snprintf(suffix,sizeof(suffix),"/%s-%02d.ppm",move,frame);
                FILE *f=std::fopen((std::string(argv[1])+suffix).c_str(),"wb");assert(f);
                std::fprintf(f,"P6\n%d %d\n255\n",kWidth,kHeight);
                for(uint16_t p:pixels) {
                    unsigned char rgb[]={static_cast<unsigned char>(((p>>11)&31)*255/31),
                        static_cast<unsigned char>(((p>>5)&63)*255/63),static_cast<unsigned char>((p&31)*255/31)};
                    std::fwrite(rgb,1,3,f);
                }
                std::fclose(f);
            }
        }
    }
    Command c;c.move=Move::ArmRaise;c.side=Side::Left;
    auto p=pose(c,3);
    assert(p.wrist[0].z>.5f && std::abs(p.wrist[0].y-p.shoulder[0].y)<.001f);
    c.move=Move::LateralRaise;p=pose(c,3);
    assert(p.wrist[0].x>.7f && p.wrist[0].y<p.shoulder[0].y);
    c.move=Move::SeatedMarch;c.side=Side::Both;
    p=pose(c,3);assert(p.knee[0].y>p.knee[1].y+.1f);
    p=pose(c,9);assert(p.knee[1].y>p.knee[0].y+.1f);
    Move invalid;assert(!parse_move("neck_circle",invalid));
    std::puts("Instructor: six rigs, constant bone lengths, fixed hips, planted feet, sides, holds and alternating marches pass.");
}
