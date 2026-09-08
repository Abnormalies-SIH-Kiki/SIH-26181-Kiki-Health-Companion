/*
  A3SCV forearm companion — V1, millimetres
  Original Waveshare case stays installed. All added hardware is printable.
  Read README.md and print fit tests before the complete wearable.
  Views: assembly, exploded, print_layout, tray, shoe, adapter, pin,
         keeper, turn_key, fit_case, fit_sensor, fit_lock, battery_gauge.
  Reference electronics and strap are NEVER included in individual part STLs.
*/

part = "assembly"; // [assembly, exploded, print_layout, tray, shoe, adapter, pin, keeper, turn_key, fit_case, fit_sensor, fit_lock, battery_gauge]
show_references = true;
$fn = 100;
eps = 0.02;

// Confirmed hardware / conservative battery envelope
case_d = 51;
case_h = 12.1;
battery_l = 43;
battery_w = 33;
battery_h = 8;
sensor_l = 18;                 // PCB dimension across the arm
sensor_w = 14;                 // PCB dimension along the arm
sensor_h = 5;                  // reserved component envelope, NOT optical height
strap_w = 22;
strap_thickness_allowance = 2.6; // measure actual fabric + stitching before printing

// Fit allowances are TOTAL additions, except named radial/per-side values.
battery_xy_clearance = 1.2;
battery_z_clearance = 0.8;
sensor_xy_clearance = 1.2;
case_radial_clearance = 0.30;
case_height_clearance = 0.25;
lid_per_side_clearance = 0.30;
pin_diametral_clearance = 0.60;
strap_width_clearance = 1.6;

// Envelope and layers: x across arm, y along arm, z away from skin.
body_w = 58;
body_l = 70;
corner_r = 13.5;
shoe_h = 2.0 + strap_thickness_allowance;
floor_h = 1.6;
battery_floor_z = shoe_h + floor_h;
lid_bottom_z = battery_floor_z + battery_h + battery_z_clearance + 1.2;
lid_h = 2;
case_z = lid_bottom_z + lid_h;
battery_y = -7.5;
strap_y = -7.5;
sensor_y = 24.3;

// Sensor face-down: seating ledge supports the PCB's component-side perimeter.
// Broad window exposes the sensor face and neighbouring components.
// Verify contact physically. Overall module height cannot determine lens height.
sensor_seat_z = 1.4;           // lower this to move PCB toward skin; re-export shoe+keeper
sensor_pcb_h = 1.6;            // provisional PCB thickness, separate from total 5mm envelope
sensor_keeper_gap = 0.2;
sensor_edge_overlap = 0.6;

pin_x = 22;
pin_y = 24;
pin_d = 4.1;
pin_head_d = 7.4;
pin_head_h = 1.8;
pin_lug_span = 6.7;
pin_lug_w = 1.6;
pin_lug_h = 1.4;
socket_roof_z = 1.9;
lock_axial_clearance = 0.15;
detent_r = 0.22;

clip_angles = [45,135,225,315];
clip_w = 5.5;
clip_t = 1.4;
clip_rim_overlap = 0.55;        // catches OUTER case rim; check it misses display glass
case_contact_interference = 0.08; // small spring-pad preload to reduce case rotation

side_text_left = "the abnormalies";
side_text_right = "Team A3SCV";
letter_font = "Orbitron:style=Bold";
letter_size = 3.1;
engrave_depth = 0.65;

assert(sensor_seat_z > 0.8, "Keep at least 0.8mm beneath sensor seating ledge");
assert(shoe_h-sensor_seat_z-sensor_pcb_h-sensor_keeper_gap >= 0.6,
       "Keeper too thin: lower sensor_seat_z or increase shoe_h");
assert(battery_y+(battery_l+battery_xy_clearance)/2+2 <=
       sensor_y-(sensor_w+sensor_xy_clearance)/2, "Need battery/sensor separation");
assert(clip_rim_overlap < 1, "Keep hooks on the outside case rim");

module rr2(w,l,r) {
    offset(r=r) square([w-2*r,l-2*r],center=true);
}
module rr(w,l,r,h) { linear_extrude(height=h) rr2(w,l,r); }
module at_pins() {
    for (x=[-pin_x,pin_x], y=[-pin_y,pin_y]) translate([x,y,0]) children();
}
module passage(z=-1,h=50) {
    translate([0,0,z]) linear_extrude(height=h)
        union() {
            circle(d=pin_d+pin_diametral_clearance);
            square([pin_lug_span+0.6,pin_lug_w+0.6],center=true);
        }
}
module pin_passages() { at_pins() passage(); }

module outer_tray() {
    // Chamfered lower edge, straight engraved sides, small upper chamfer.
    hull() {
        translate([0,0,shoe_h]) rr(body_w-1.2,body_l-1.2,corner_r-0.6,0.1);
        translate([0,0,shoe_h+1.0]) rr(body_w,body_l,corner_r,0.1);
    }
    translate([0,0,shoe_h+1.0])
        rr(body_w,body_l,corner_r,case_z-shoe_h-2.0);
    hull() {
        translate([0,0,case_z-1.1]) rr(body_w,body_l,corner_r,0.1);
        translate([0,0,case_z-0.1]) rr(body_w-0.5,body_l-0.5,corner_r-0.25,0.1);
    }
}

module side_engraving() {
    for (s=[-1,1])
        translate([s*(body_w/2-engrave_depth),0,(shoe_h+case_z)/2+0.4])
            rotate([90,0,s*90])
                linear_extrude(height=engrave_depth+0.3)
                    offset(delta=0.06)
                        text(s<0?side_text_left:side_text_right,
                             font=letter_font,size=letter_size,
                             halign="center",valign="center",spacing=1.02);
}

module tray() {
    difference() {
        union() {
            difference() {
                outer_tray();
                // Battery pocket, with rounded corners and clearance at pouch edges.
                translate([0,battery_y,battery_floor_z])
                    rr(battery_w+battery_xy_clearance,
                       battery_l+battery_xy_clearance,1.3,case_z);
                // Upper shared wiring space; 3mm-high battery locating walls remain.
                translate([0,0,battery_floor_z+3]) rr(body_w-8,body_l-8,9.5,case_z);
                // Adapter seats in this rebate; pin bosses remain below its underside.
                translate([0,0,lid_bottom_z]) rr(body_w-3.6,body_l-3.6,corner_r-1.8,5);
                // Low wiring gutters around the long sides of the battery.
                for (s=[-1,1]) translate([s*21,battery_y,battery_floor_z+0.4])
                    rr(5.4,39,1.4,case_z);
                // Sensor back / solder access, leaving perimeter to capture keeper.
                translate([0,sensor_y,shoe_h-eps]) rr(14,10,1,case_z);
                // Protected route from sensor backside into shared wiring space.
                translate([-3.5,14.5,battery_floor_z+0.4]) cube([7,12,case_z]);
            }
            // Full-height structural bosses bypass battery and sensor.
            at_pins() translate([0,0,shoe_h]) cylinder(d=8.5,h=lid_bottom_z-shoe_h);
        }
        pin_passages();
        side_engraving();
    }
}

module lock_socket() {
    passage();
    // Underside circular counterbore: lug is recessed away from skin.
    translate([0,0,-eps]) cylinder(d=8.5,h=socket_roof_z+eps);
    // Two shallow detent seats at the locked (90 degree) position.
    for (s=[-1,1]) translate([0,s*2.9,socket_roof_z]) sphere(r=0.34);
}

module shoe() {
    difference() {
        union() {
            // Flat print face, bevelled perimeter. No hidden underside supports.
            hull() {
                rr(body_w-2,body_l-2,corner_r-1,0.1);
                translate([0,0,0.9]) rr(body_w-0.6,body_l-0.6,corner_r-0.3,0.1);
            }
            translate([0,0,0.9]) rr(body_w-0.6,body_l-0.6,corner_r-0.3,shoe_h-0.9);
        }
        // Open-top channel becomes an enclosed strap tunnel when tray is installed.
        translate([-body_w,strap_y-(strap_w+strap_width_clearance)/2,2])
            cube([body_w*2,strap_w+strap_width_clearance,shoe_h]);
        // Chamfered channel entrances protect the fabric edge.
        for (s=[-1,1]) translate([s*(body_w/2-1),strap_y,2.0])
            rotate([0,45,0]) cube([2,strap_w+strap_width_clearance,2],center=true);
        // Sensor cavity and wide front aperture: no plastic across the optical face.
        translate([0,sensor_y,sensor_seat_z])
            rr(sensor_l+sensor_xy_clearance,sensor_w+sensor_xy_clearance,0.8,shoe_h+1);
        translate([0,sensor_y,-eps])
            rr(sensor_l-2*sensor_edge_overlap,sensor_w-2*sensor_edge_overlap,0.8,sensor_seat_z+2*eps);
        at_pins() lock_socket();
    }
}

module keeper() {
    // Loose frame clamped by the main tray; no screws, glue or PCB drilling.
    h = shoe_h-sensor_seat_z-sensor_pcb_h-sensor_keeper_gap;
    difference() {
        rr(sensor_l+sensor_xy_clearance-0.4,sensor_w+sensor_xy_clearance-0.4,0.7,h);
        translate([0,0,-eps]) rr(14.5,10.5,0.6,h+2*eps);
        // One wire exit preserves a connected C-frame; rotate keeper 180 if needed.
        translate([-8.5,0,h/2]) cube([5,6,h+1],center=true);
    }
}

module case_clip(a) {
    r=case_d/2+case_radial_clearance;
    top=case_z+case_h+case_height_clearance;
    ri=case_d/2-clip_rim_overlap;
    contact_r=case_d/2-case_contact_interference;
    rotate([0,0,a]) rotate([90,0,0])
        linear_extrude(height=clip_w,center=true)
            polygon([[r,lid_bottom_z],[r+clip_t,lid_bottom_z],
                     [r+clip_t,top+0.8],[r+0.15,top+0.8],
                     [ri,top+0.25],[ri,top],[r,top],
                     [r,case_z+8.5],[contact_r,case_z+7],
                     [r,case_z+5.5]]);
}

module adapter() {
    difference() {
        union() {
            translate([0,0,lid_bottom_z])
                rr(body_w-3.6-2*lid_per_side_clearance,
                   body_l-3.6-2*lid_per_side_clearance,
                   corner_r-1.8-lid_per_side_clearance,lid_h);
            for(a=clip_angles) case_clip(a);
        }
        translate([0,0,lid_bottom_z-eps]) cylinder(d=46,h=lid_h+2*eps);
        translate([-19,0,lid_bottom_z-eps]) rr(8,30,1.2,lid_h+2*eps);
        pin_passages();
    }
}

module pin() {
    // Print head-down. In assembly it is inverted, with lugs below the shoe.
    lug_z=pin_head_h+case_z-(socket_roof_z-lock_axial_clearance);
    difference() {
        union() {
            cylinder(d=pin_head_d,h=pin_head_h);
            translate([0,0,pin_head_h-eps]) cylinder(d=pin_d,h=lug_z+pin_lug_h-pin_head_h);
            translate([0,0,lug_z]) linear_extrude(height=pin_lug_h)
                offset(r=0.35) square([pin_lug_span-0.7,pin_lug_w-0.7],center=true);
            for(s=[-1,1]) translate([s*2.9,0,lug_z]) sphere(r=detent_r);
        }
        // Printed turn-key fits this slot. Slot orientation also indicates lock state.
        translate([-2.7,-0.7,-eps]) cube([5.4,1.4,0.75+eps]);
    }
}

module assembled_pin() {
    translate([0,0,case_z+pin_head_h]) rotate([180,0,90]) pin();
}

module turn_key() {
    linear_extrude(height=1.1) union() {
        translate([0,10]) circle(d=14);
        translate([-2.4,0]) square([4.8,12]);
    }
}

module reference_case() {
    color([0.065,0.075,0.085]) difference() {
        translate([0,0,case_z]) cylinder(d=case_d,h=case_h);
        // Indicative side access only. The official case itself is not being remodelled.
        for(s=[-1,1]) translate([0,s*case_d/2,case_z+case_h/2])
            cube([s>0?10:16,4,3.5],center=true);
    }
    color([0.025,0.045,0.06]) translate([0,0,case_z+case_h+0.02]) cylinder(d=47.8,h=0.05);
    color([0.15,0.85,0.80]) translate([0,3,case_z+case_h+0.08])
        linear_extrude(height=0.02) text("15:28",font=letter_font,size=8,halign="center",valign="center");
    color([0.7,0.85,0.88]) translate([0,-8,case_z+case_h+0.08])
        linear_extrude(height=0.02) text("A3SCV",font=letter_font,size=3.3,halign="center",valign="center");
}

module reference_battery() {
    color([0.68,0.72,0.76]) translate([0,battery_y,battery_floor_z]) rr(battery_w,battery_l,1,battery_h);
    color([0.90,0.66,0.12]) translate([0,battery_y+battery_l/2-2,battery_floor_z]) rr(battery_w,4,0.5,battery_h);
}
module reference_sensor() {
    // Approximate only: component positions and optical projection need physical verification.
    color([0.08,0.4,0.23]) translate([0,sensor_y,sensor_seat_z]) rr(sensor_l,sensor_w,0.4,sensor_pcb_h);
    color([0.12,0.13,0.14]) translate([-2.8,sensor_y-1.65,sensor_seat_z-1.55]) cube([5.6,3.3,1.55]);
}
module reference_strap() {
    color([0.02,0.27,0.40]) translate([-65,strap_y-strap_w/2,2.2]) cube([130,strap_w,1.8]);
}

module assembly(explode=0) {
    color([0.22,0.25,0.29]) translate([0,0,-explode]) shoe();
    color([0.13,0.16,0.20]) tray();
    color([0.29,0.34,0.39]) translate([0,0,explode]) adapter();
    color([0.05,0.5,0.6]) at_pins() translate([0,0,explode*2]) assembled_pin();
    color([0.3,0.36,0.4]) translate([0,sensor_y,shoe_h-(shoe_h-sensor_seat_z-sensor_pcb_h-sensor_keeper_gap)-explode]) keeper();
    if(show_references) {
        translate([0,0,2.5*explode]) reference_case();
        reference_battery();
        translate([0,0,-explode]) reference_sensor();
        translate([0,0,-explode]) reference_strap();
    }
}

module fit_case() {
    // Same seat and clips as full adapter, less filament. Test off-arm, over a table.
    translate([0,0,-lid_bottom_z]) intersection() {
        adapter();
        translate([0,0,lid_bottom_z-eps]) cylinder(d=56,h=case_h+lid_h+3);
    }
}
module fit_sensor() {
    // Cut-out of actual shoe/keeper region, with a wider temporary handling border.
    intersection() {
        translate([0,-sensor_y,0]) shoe();
        translate([0,0,-eps]) rr(26,21,2,shoe_h+1);
    }
}
module fit_lock() {
    difference() {
        rr(15,15,3,case_z);
        lock_socket();
    }
}
module battery_gauge() {
    difference() {
        rr(battery_w+battery_xy_clearance+3.2,battery_l+battery_xy_clearance+3.2,2,3);
        translate([0,0,-eps]) rr(battery_w+battery_xy_clearance,battery_l+battery_xy_clearance,1.3,4);
    }
}

if(part=="assembly") assembly();
else if(part=="exploded") assembly(12);
else if(part=="tray") translate([0,0,-shoe_h]) tray();
else if(part=="shoe") shoe();
else if(part=="adapter") translate([0,0,-lid_bottom_z]) adapter();
else if(part=="pin") pin();
else if(part=="keeper") keeper();
else if(part=="turn_key") turn_key();
else if(part=="fit_case") fit_case();
else if(part=="fit_sensor") fit_sensor();
else if(part=="fit_lock") fit_lock();
else if(part=="battery_gauge") battery_gauge();
else if(part=="print_layout") {
    translate([-35,40,-shoe_h]) tray();
    translate([35,40,0]) shoe();
    translate([-35,-40,-lid_bottom_z]) adapter();
    translate([25,-55,0]) keeper();
    for(i=[0:3]) translate([20+i*10,-28,0]) pin();
    translate([47,-65,0]) turn_key();
}
else assert(false,"Unknown part selection");

echo("Body footprint",body_w,body_l);
echo("Height to original case face",case_z+case_h);
echo("Max height incl. clip lead-in",case_z+case_h+case_height_clearance+0.8);
echo("Battery pocket",battery_w+battery_xy_clearance,battery_l+battery_xy_clearance,battery_h+battery_z_clearance);
echo("Strap tunnel",strap_w+strap_width_clearance,strap_thickness_allowance);
