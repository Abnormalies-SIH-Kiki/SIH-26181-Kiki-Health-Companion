#include "kiki_instructor_model.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>

namespace kiki::instructor {
namespace {
constexpr float kPi = 3.141592654f;
constexpr float kScale = 165;
constexpr uint32_t kSkin = 0xDCA17E, kTop = 0x36CFBB, kPants = 0x49496C;
Vec add(Vec a, Vec b) { return {a.x+b.x,a.y+b.y,a.z+b.z}; }
Vec sub(Vec a, Vec b) { return {a.x-b.x,a.y-b.y,a.z-b.z}; }
Vec mul(Vec a, float s) { return {a.x*s,a.y*s,a.z*s}; }
float dot(Vec a, Vec b) { return a.x*b.x+a.y*b.y+a.z*b.z; }
Vec cross(Vec a, Vec b) { return {a.y*b.z-a.z*b.y,a.z*b.x-a.x*b.z,a.x*b.y-a.y*b.x}; }
Vec unit(Vec v) { return mul(v,1/std::sqrt(std::max(.000001f,dot(v,v)))); }
Vec rotate(Vec v, float a) { return {v.x*std::cos(a)+v.z*std::sin(a),v.y,-v.x*std::sin(a)+v.z*std::cos(a)}; }
Vec view(Vec v) {
    v=rotate(v,-.32f); // fixed three-quarter view; both arms stay legible
    return {v.x,v.y*.985585f-v.z*.169182f,v.y*.169182f+v.z*.985585f};
}
uint16_t rgb(uint32_t c, float light=1) {
    const int r=std::min(255, int(((c>>16)&255)*light));
    const int g=std::min(255, int(((c>>8)&255)*light));
    const int b=std::min(255, int((c&255)*light));
    return uint16_t(((r>>3)<<11)|((g>>2)<<5)|(b>>3));
}

// Ray/ellipsoid intersections, with a per-pixel depth buffer and analytic
// surface normals: rounded 3D volumes, not flat circles or painter-order limbs.
// Scratch is supplied by the caller; rendering allocates nothing.
struct Renderer {
    uint16_t *pixels;
    float *depth;
    void ellipsoid(Vec centre, Vec a, Vec b, Vec c, uint32_t colour) {
        centre=view(centre); a=view(a); b=view(b); c=view(c);
        const float rx=std::sqrt(a.x*a.x+b.x*b.x+c.x*c.x);
        const float ry=std::sqrt(a.y*a.y+b.y*b.y+c.y*c.y);
        a=mul(a,1/dot(a,a)); b=mul(b,1/dot(b,b)); c=mul(c,1/dot(c,c));
        const float xx=a.x*a.x+b.x*b.x+c.x*c.x;
        const float yy=a.y*a.y+b.y*b.y+c.y*c.y;
        const float zz=a.z*a.z+b.z*b.z+c.z*c.z;
        const float xy=a.x*a.y+b.x*b.y+c.x*c.y;
        const float xz=a.x*a.z+b.x*b.z+c.x*c.z;
        const float yz=a.y*a.z+b.y*b.z+c.y*c.z;
        const int x0=std::max(0,int(160+(centre.x-rx)*kScale)-1);
        const int x1=std::min(kWidth-1,int(160+(centre.x+rx)*kScale)+1);
        const int y0=std::max(0,int(279-(centre.y+ry)*kScale)-1);
        const int y1=std::min(kHeight-1,int(279-(centre.y-ry)*kScale)+1);
        for(int py=y0;py<=y1;++py) {
            const float y=(279-py)/kScale-centre.y;
            for(int px=x0;px<=x1;++px) {
                const float x=(px-160)/kScale-centre.x;
                const float q=x*xz+y*yz;
                const float d=q*q-zz*(xx*x*x+2*xy*x*y+yy*y*y-1);
                if(d<0) continue;
                const float z=(-q+std::sqrt(d))/zz;
                const int i=py*kWidth+px;
                if(z+centre.z<=depth[i]) continue;
                depth[i]=z+centre.z;
                const Vec n=unit({xx*x+xy*y+xz*z,xy*x+yy*y+yz*z,xz*x+yz*y+zz*z});
                const float diffuse=std::max(0.f,-.38f*n.x+.62f*n.y+.68f*n.z);
                const float rim=std::max(0.f,n.x)*.12f;
                const float h=std::max(0.f,-.2f*n.x+.35f*n.y+.915f*n.z);
                const float h2=h*h, h4=h2*h2, h8=h4*h4;
                const float spec=h8*h8*h4*.2f;
                pixels[i]=rgb(colour,.48f+.54f*diffuse+rim+spec);
            }
        }
    }
    void ball(Vec p, Vec size, uint32_t c, float yaw=0) {
        ellipsoid(p,rotate({size.x,0,0},yaw),{0,size.y,0},rotate({0,0,size.z},yaw),c);
    }
    void limb(Vec a, Vec b, float radius, uint32_t colour) {
        const Vec d=sub(b,a), axis=unit(d);
        const Vec u=unit(cross(axis,std::abs(axis.z)<.9f?Vec{0,0,1}:Vec{1,0,0}));
        const Vec v=cross(axis,u);
        ellipsoid(mul(add(a,b),.5f),mul(u,radius),mul(axis,std::sqrt(dot(d,d))*.5f+radius*.55f),mul(v,radius),colour);
    }
};
float excursion(float t, float period, bool hold) {
    if(t<0) return 0; // preparation: neutral pose, no implied exercise
    if(hold) return .5f-.5f*std::cos(kPi*std::clamp(t/2.f,0.f,1.f));
    return .5f-.5f*std::cos(2*kPi*t/period);
}
} // namespace

bool parse_move(const char *name, Move &out) {
    const char *names[]={"arm_raise","lateral_raise","elbow_curl","shoulder_roll","seated_march","torso_twist"};
    for(int i=0;i<6;++i) if(name && std::strcmp(name,names[i])==0) {out=static_cast<Move>(i);return true;}
    return false;
}
const char *move_title(Move move) {
    const char *titles[]={"Front arm raise","Side arm raise","Elbow curl","Shoulder roll","Seated march","Torso turn"};
    return titles[static_cast<int>(move)];
}

Pose pose(const Command &cmd, float seconds) {
    Pose p{};
    const float period=std::clamp(cmd.period,4.f,12.f);
    const float e=excursion(seconds,period,cmd.hold);
    for(int i=0;i<2;++i) {
        // Index 0 is the instructor's anatomical left (screen right).
        const float sign=i==0?1.f:-1.f;
        const bool selected=cmd.side==Side::Both || (i==0)==(cmd.side==Side::Left);
        float m=selected?e:0;
        p.hip[i]={sign*.12f,.60f,0};
        p.knee[i]={sign*.14f,.53f,.424f};
        p.ankle[i]={sign*.14f,.10f,.424f};
        p.shoulder[i]={sign*.205f,1.135f,0};
        Vec upper={sign*.045f,-.292f,.012f}, lower={0,-.265f,.035f};
        if(cmd.move==Move::ArmRaise) {
            const float a=m*kPi*.5f;
            upper={sign*.045f,-.292f*std::cos(a),.292f*std::sin(a)};
            lower={0,-.267f*std::cos(a),.267f*std::sin(a)};
        } else if(cmd.move==Move::LateralRaise) {
            const float a=.10f+m*1.30f;
            upper={sign*.296f*std::sin(a),-.296f*std::cos(a),0};
            lower={sign*.267f*std::sin(a),-.267f*std::cos(a),0};
        } else if(cmd.move==Move::ElbowCurl) {
            const float a=.13f+m*2.15f;
            lower={0,-.267f*std::cos(a),.267f*std::sin(a)};
        } else if(cmd.move==Move::ShoulderRoll && selected && seconds>=0) {
            const float a=cmd.hold?kPi:seconds/period*2*kPi;
            p.shoulder[i].y+=.035f*(1-std::cos(a));
            p.shoulder[i].z-=.028f*std::sin(a);
        } else if(cmd.move==Move::SeatedMarch) {
            if(cmd.side==Side::Both && seconds>=0 && !cmd.hold) {
                const int active=int(seconds/period)%2;
                m=i==active?e:0;
            }
            const float a=-.1635f+m*.34f;
            p.knee[i]=add(p.hip[i],{0,.43f*std::sin(a),.43f*std::cos(a)});
            p.ankle[i]=add(p.knee[i],{0,-.43f,0});
        }
        p.elbow[i]=add(p.shoulder[i],upper);
        p.wrist[i]=add(p.elbow[i],lower);
    }
    if(cmd.move==Move::TorsoTwist) {
        float sign=cmd.side==Side::Right?-1.f:1.f;
        if(cmd.side==Side::Both && seconds>=0 && !cmd.hold) sign=int(seconds/period)%2==0?1.f:-1.f;
        p.twist=sign*e*.40f;
        for(int i=0;i<2;++i) {
            const float s=i==0?1.f:-1.f;
            p.shoulder[i]=rotate(p.shoulder[i],p.twist);
            p.elbow[i]=rotate({s*.18f,.89f,.16f},p.twist);
            p.wrist[i]=rotate({-s*.03f,1.05f,.17f},p.twist);
        }
    }
    return p;
}

void render(uint16_t *pixels, float *depth, const Command &cmd, float seconds) {
    for(int y=0;y<kHeight;++y) for(int x=0;x<kWidth;++x) {
        const float dx=(x-160)/150.f, dy=(y-275)/25.f;
        const float shadow=std::max(0.f,1-dx*dx-dy*dy);
        pixels[y*kWidth+x]=rgb(0x111D2B,1-.35f*shadow);
        depth[y*kWidth+x]=-100;
    }
    Renderer r{pixels,depth};
    const Pose p=pose(cmd,seconds);
    // Stable armless chair, a softly lit seat and four planted legs.
    for(float x:{-.19f,.19f}) for(float z:{-.13f,.30f})
        r.limb({x,.05f,z},{x,.49f,z},.019f,0x63788A);
    r.ball({0,.515f,.08f},{.255f,.045f,.285f},0x263A50);
    for(float x:{-.20f,.20f}) r.limb({x,.50f,-.15f},{x,1.03f,-.15f},.017f,0x63788A);
    r.ball({0,.92f,-.17f},{.235f,.15f,.035f},0x263A50);
    for(int i=0;i<2;++i) {
        r.limb(p.hip[i],p.knee[i],.089f,kPants);
        r.ball(p.knee[i],{.071f,.07f,.071f},kPants);
        r.limb(p.knee[i],p.ankle[i],.055f,kPants);
        r.ball(add(p.ankle[i],{0,-.033f,.052f}),{.065f,.047f,.128f},0xDCE8E9);
        r.ball(add(p.ankle[i],{0,-.060f,.052f}),{.067f,.020f,.132f},0x6E9C9E);
    }
    r.ball({0,.64f,0},{.184f,.12f,.124f},kPants);
    r.ball({0,.935f,0},{.181f,.283f,.108f},kTop,p.twist);
    r.ball({0,1.205f,0},{.066f,.020f,.060f},0x208F8E,p.twist);
    r.ball({0,1.224f,0},{.054f,.072f,.057f},kSkin);
    for(int i=0;i<2;++i) {
        const Vec a=p.shoulder[i], b=p.elbow[i], c=p.wrist[i];
        r.limb(rotate({i==0?.115f:-.115f,1.13f,0},p.twist),a,.060f,kTop);
        r.ball(a,{.067f,.073f,.067f},kTop);
        r.limb(a,add(a,mul(sub(b,a),.30f)),.067f,kTop);
        r.limb(add(a,mul(sub(b,a),.21f)),b,.046f,kSkin);
        r.ball(b,{.044f,.044f,.044f},kSkin);
        r.limb(b,c,.036f,kSkin);
        const Vec d=unit(sub(c,b));
        const Vec hand=add(c,mul(d,.044f));
        r.limb(c,add(c,mul(d,.078f)),.029f,kSkin);
        // Grouped fingers with a separate thumb retain a readable hand silhouette.
        r.ball(add(hand,{i==0?-.023f:.023f,0,.018f}),{.016f,.027f,.018f},kSkin);
    }
    auto head=[&](Vec local,Vec size,uint32_t colour) {
        r.ball(add(rotate(local,p.twist),{0,1.363f,0}),size,colour,p.twist);
    };
    head({0,0,-.036f},{.111f,.139f,.094f},0x30252C);
    head({0,-.008f,.015f},{.094f,.122f,.091f},kSkin);
    head({0,.094f,-.005f},{.110f,.051f,.102f},0x30252C);
    head({0,.071f,-.124f},{.071f,.066f,.057f},0x30252C); // hair bun
    for(float s:{-1.f,1.f}) {
        head({s*.094f,-.012f,.018f},{.018f,.032f,.022f},kSkin);
        head({s*.037f,.005f,.098f},{.021f,.012f,.008f},0xFFF5E7);
        head({s*.035f,.006f,.105f},{.009f,.010f,.006f},0x283343);
        head({s*.033f,.009f,.109f},{.003f,.003f,.003f},0xFFFFFF);
        head({s*.038f,.032f,.094f},{.026f,.006f,.010f},0x48302F);
        head({s*.052f,-.034f,.086f},{.022f,.010f,.008f},0xD58E7A);
    }
    head({0,-.014f,.102f},{.014f,.021f,.018f},kSkin);
    head({0,-.062f,.092f},{.027f,.007f,.006f},0x9B514F);
    head({0,-.057f,.095f},{.019f,.004f,.003f},0xFFF0DD);
}
} // namespace kiki::instructor
