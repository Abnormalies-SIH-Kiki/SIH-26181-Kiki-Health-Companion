/*
  A3SCV forearm companion — final print revision, millimetres
  Original Waveshare case stays installed. All added hardware is printable.
  Read PRINT_INSTRUCTIONS.md. Physical fit and strength are not certified by CAD.
  Views: assembly, exploded, print_layout, tray, shoe, adapter, pin,
         keeper, turn_key, fit_case, fit_sensor, fit_lock, battery_gauge.
  Reference electronics and strap are NEVER included in individual part STLs.
*/

part = "shoe"; // [assembly, exploded, print_layout, tray, shoe, adapter, pin, keeper, turn_key, shim_032, shim_064, shim_096]
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
strap_thickness_allowance = 3.0; // measure actual fabric + stitching before printing

// Fit allowances are TOTAL additions, except named radial/per-side values.
battery_xy_clearance = 2.0;
battery_z_clearance = 1.2;
sensor_xy_clearance = 2.0;
case_radial_clearance = 0.50;
case_height_clearance = 0.70;
lid_per_side_clearance = 0.40;
pin_diametral_clearance = 1.00;
strap_width_clearance = 2.0;

// Envelope and layers: x across arm, y along arm, z away from skin.
body_w = 60;
body_l = 72;
corner_r = 14.5;
shoe_h = 2.0 + strap_thickness_allowance;
floor_h = 1.6;
battery_floor_z = shoe_h + floor_h;
lid_bottom_z = battery_floor_z + battery_h + battery_z_clearance + 1.5;
lid_h = 2.4;
case_z = lid_bottom_z + lid_h;
battery_y = -7.5;
strap_y = -7.5;
sensor_y = 25.2;

// Sensor face-down: seating ledge supports the PCB's component-side perimeter.
// Broad window exposes the sensor face and neighbouring components.
// Verify contact physically. Overall module height cannot determine lens height.
sensor_seat_z = 1.4;           // lower this to move PCB toward skin; re-export shoe+keeper
sensor_pcb_h = 1.6;            // provisional PCB thickness, separate from total 5mm envelope
sensor_keeper_gap = 0.35;
sensor_edge_overlap = 0.6;

pin_x = 22.8;
pin_y = 24;
pin_d = 4.1;
pin_head_d = 11.4;
pin_head_h = 2.0;
pin_lug_span = 7.3;
pin_lug_w = 2.0;
pin_lug_h = 1.8;
socket_roof_z = 2.2;
lock_axial_clearance = 0.12;
detent_r = 0.50;
head_spring_t = 0.8;
installed_shim_h = 0.64;       // preview only; supplied 0.32 / 0.64 / 0.96 mm shims

clip_angles = [30,150,210,330]; // clear the larger pin heads and the port axes
clip_w = 7.0;
clip_t = 1.4;
clip_rim_overlap = 1.0;        // catches OUTER case rim; check it misses display glass
case_contact_interference = 0.18; // small spring-pad preload to reduce case rotation

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
assert(clip_rim_overlap <= 1, "Keep hooks on the outside case rim");

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
            square([pin_lug_span+0.8,pin_lug_w+0.8],center=true);
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
                       battery_l+battery_xy_clearance,0.7,case_z);
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
            at_pins() translate([0,0,shoe_h]) cylinder(d=9.6,h=lid_bottom_z-shoe_h);
        }
        pin_passages();
        side_engraving();
    }
}

module lock_socket() {
    passage();
    // Underside circular counterbore: lug is recessed away from skin.
    translate([0,0,-eps]) difference() {
        cylinder(d=9.3,h=socket_roof_z+eps);
        // Hard stops just beyond the 90 degree detent prevent a second quarter-turn.
        for(a=[112,292]) rotate([0,0,a]) linear_extrude(height=socket_roof_z+2*eps)
            polygon([[2.6,0],[5,0],[5*cos(25),5*sin(25)],[2.6*cos(25),2.6*sin(25)]]);
    }
    // Two shallow detent seats at the locked (90 degree) position.
    for (s=[-1,1]) translate([0,s*2.9,socket_roof_z]) sphere(r=0.72);
}

// Speaker-wire revision: LONG SIDE exit only. Existing tray and keeper unchanged.
// Open-top 5mm-wide x 3mm-deep route; tray closes its roof.
// Align the keeper's C opening toward negative X (the channel side).
module speaker_wire_channel() {
    translate([0,0,2.0]) linear_extrude(height=shoe_h-2.0+eps)
        union() {
            hull() {
                translate([-8,sensor_y]) circle(r=2.5,$fn=40);
                translate([-12.5,sensor_y]) circle(r=2.5,$fn=40);
            }
            hull() {
                translate([-12.5,sensor_y]) circle(r=2.5,$fn=40);
                translate([-12.5,12]) circle(r=2.5,$fn=40);
            }
            // Turn toward the long side, between the strap tunnel and lock socket.
            hull() {
                translate([-12.5,12]) circle(r=2.5,$fn=40);
                translate([-body_w/2-4,12]) circle(r=2.5,$fn=40);
            }
        }
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
        speaker_wire_channel();
    }
}

module keeper() {
    // Loose frame clamped by the main tray; no screws, glue or PCB drilling.
    h = shoe_h-sensor_seat_z-sensor_pcb_h-sensor_keeper_gap;
    difference() {
        rr(sensor_l+sensor_xy_clearance-0.8,sensor_w+sensor_xy_clearance-0.8,0.7,h);
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
                     [r,case_z+10.2],[contact_r,case_z+9],
                     [r,case_z+7.8]]);
    // 0.9mm external root fillet, preserving the free upper beam.
    rotate([0,0,a]) difference() {
        translate([r+clip_t,-clip_w/2,case_z-0.3]) cube([0.9,clip_w,1.2]);
        translate([r+clip_t+0.9,clip_w/2+eps,case_z+0.9])
            rotate([90,0,0]) cylinder(r=0.9,h=clip_w+2*eps,$fn=40);
    }
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
        translate([-19,0,lid_bottom_z-eps]) rr(8,22,1.2,lid_h+2*eps);
        pin_passages();
    }
}

module spring_arc(a0,a1,r=3.45,w=1.2) {
    for(a=[a0:5:a1-5]) hull() {
        translate([r*cos(a),r*sin(a)]) circle(d=w,$fn=24);
        translate([r*cos(a+5),r*sin(a+5)]) circle(d=w,$fn=24);
    }
}

module spring_head() {
    // Outer ring bears on the adapter. Two long, flat leaves let the centre
    // move axially as the 0.5 mm detent bumps pass under the socket roof.
    difference() {
        cylinder(d=pin_head_d,h=pin_head_h);
        translate([0,0,-eps]) cylinder(r=4.7,h=pin_head_h+2*eps);
    }
    linear_extrude(height=head_spring_t) for(a=[45,225]) {
        spring_arc(a,a+150);
        hull() {
            translate([2*cos(a),2*sin(a)]) circle(d=1.2,$fn=24);
            translate([3.45*cos(a),3.45*sin(a)]) circle(d=1.2,$fn=24);
        }
        hull() {
            translate([3.45*cos(a+150),3.45*sin(a+150)]) circle(d=1.2,$fn=24);
            translate([5*cos(a+150),5*sin(a+150)]) circle(d=1.2,$fn=24);
        }
    }
}

module pin_core() {
    lug_z=pin_head_h+case_z-(socket_roof_z-lock_axial_clearance);
    difference() {
        union() {
            cylinder(d=4.4,h=pin_head_h);
            translate([0,0,pin_head_h-eps]) cylinder(d=pin_d,h=lug_z+pin_lug_h-pin_head_h);
            translate([0,0,lug_z]) linear_extrude(height=pin_lug_h)
                offset(r=0.35) square([pin_lug_span-0.7,pin_lug_w-0.7],center=true);
            for(s=[-1,1]) translate([s*2.9,0,lug_z]) sphere(r=detent_r);
        }
        // Drive the centre, not the spring ring. Slot misses the spring roots.
        translate([-1.8,-0.8,-eps]) cube([3.6,1.6,0.8+eps]);
    }
}

module pin() { union() { spring_head(); pin_core(); } }

module assembled_pin() {
    translate([0,0,case_z+pin_head_h]) rotate([180,0,90]) pin();
}

module turn_key() {
    linear_extrude(height=1.0) union() {
        translate([0,10]) circle(d=14);
        translate([-1.4,0]) square([2.8,12]);
    }
}

module case_shim(h=0.64,n=2) {
    // Three complete seating options are supplied in the full print, not test coupons.
    difference() {
        cylinder(d=51.2,h=h);
        translate([0,0,-eps]) cylinder(d=47,h=h+2*eps);
        for(i=[0:n-1]) rotate([0,0,i*8]) translate([25.6,0,-eps])
            cylinder(r=0.55,h=h+2*eps,$fn=24);
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
    color([0.16,0.55,0.60]) translate([0,0,case_z+1.8*explode]) case_shim(installed_shim_h,2);
    color([0.05,0.5,0.6]) at_pins() translate([0,0,explode*2]) assembled_pin();
    color([0.3,0.36,0.4]) translate([0,sensor_y,shoe_h-(shoe_h-sensor_seat_z-sensor_pcb_h-sensor_keeper_gap)-explode]) keeper();
    if(show_references) {
        translate([0,0,2.5*explode+installed_shim_h]) reference_case();
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
else if(part=="shim_032") case_shim(0.32,1);
else if(part=="shim_064") case_shim(0.64,2);
else if(part=="shim_096") case_shim(0.96,3);
else if(part=="pin_core") pin_core();
else if(part=="fit_case") fit_case();
else if(part=="fit_sensor") fit_sensor();
else if(part=="fit_lock") fit_lock();
else if(part=="battery_gauge") battery_gauge();
else if(part=="print_layout") {
    translate([-40,50,-shoe_h]) tray();
    translate([40,50,0]) shoe();
    translate([-40,-30,-lid_bottom_z]) adapter();
    translate([28,-30,0]) case_shim(0.32,1);
    translate([85,-30,0]) case_shim(0.64,2);
    translate([-40,-93,0]) case_shim(0.96,3);
    translate([12,-90,0]) keeper();
    for(i=[0:5]) translate([38+(i%3)*18,-78-floor(i/3)*20,0]) pin();
    translate([96,-103,0]) turn_key();
}
else assert(false,"Unknown part selection");

echo("Body footprint",body_w,body_l);
echo("Height to original case face",case_z+case_h);
echo("Max height incl. clip lead-in",case_z+case_h+case_height_clearance+0.8);
echo("Battery pocket",battery_w+battery_xy_clearance,battery_l+battery_xy_clearance,battery_h+battery_z_clearance);
echo("Strap tunnel",strap_w+strap_width_clearance,strap_thickness_allowance);
