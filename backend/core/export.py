"""
ECDIS Route Export Module (IEC 61174 RTZ & GPX 1.1)
===================================================
Converts SeaPath passage plans and structured waypoints into:
1. GPX 1.1 (GPS Exchange Format) for commercial and consumer plotters.
2. RTZ 1.1 (IEC 61174:2015 Route Exchange Format) for IMO-compliant ECDIS systems.
"""

from typing import List, Dict, Any, Optional
import xml.etree.ElementTree as ET
from xml.dom import minidom


def _format_xml(root: ET.Element) -> str:
    """Pretty-print XML element tree with standard declaration."""
    raw_str = ET.tostring(root, encoding="utf-8")
    reparsed = minidom.parseString(raw_str)
    return reparsed.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")


def export_to_gpx(
    waypoints: List[Dict[str, Any]],
    route_coords: List[List[float]],
    draft: float,
    speed_knots: float,
    ukc: float,
    route_name: str = "SeaPath_Passage_Plan",
) -> str:
    """
    Generate GPX 1.1 XML document containing route waypoints and high-resolution track.
    """
    gpx = ET.Element("gpx", {
        "version": "1.1",
        "creator": "SeaPath ECDIS Passage Planner",
        "xmlns": "http://www.topografix.com/GPX/1/1",
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "xsi:schemaLocation": "http://www.topografix.com/GPX/1/1 http://www.topografix.com/GPX/1/1/gpx.xsd",
    })

    metadata = ET.SubElement(gpx, "metadata")
    name_el = ET.SubElement(metadata, "name")
    name_el.text = route_name
    desc_el = ET.SubElement(metadata, "desc")
    desc_el.text = f"Draft: {draft:.1f}m | Speed: {speed_knots:.1f} kts | Required UKC: {ukc:.1f}m"

    # Route with navigational waypoints
    rte = ET.SubElement(gpx, "rte")
    rte_name = ET.SubElement(rte, "name")
    rte_name.text = route_name

    for idx, wp in enumerate(waypoints, start=1):
        lat = wp["lat"]
        lon = wp["lon"]
        rtept = ET.SubElement(rte, "rtept", {"lat": f"{lat:.6f}", "lon": f"{lon:.6f}"})
        
        pt_name = ET.SubElement(rtept, "name")
        pt_name.text = f"WP{idx:02d}"

        brg = wp.get("leg_bearing_deg", 0.0)
        dist = wp.get("leg_distance_nm", 0.0)
        depth = wp.get("depth_m", 0.0)
        ukc_val = wp.get("clearance_m", 0.0)
        turn_ang = wp.get("turn_angle_deg", 0.0)
        radius = wp.get("turn_radius_m", 0.0)

        rot = wp.get("rot_deg_min", 0.0)
        wop = wp.get("wop_distance_m", 0.0)

        cmt = ET.SubElement(rtept, "cmt")
        cmt.text = (
            f"Course: {brg:.1f}°T | Leg: {dist:.2f} NM | "
            f"Depth: {depth:.1f}m | UKC: {ukc_val:.1f}m | Turn: {turn_ang:.1f}° (R={radius:.0f}m, ROT={rot:.1f}°/min, WOP={wop:.0f}m)"
        )

    # Detailed track (high-resolution bathymetric trajectory)
    trk = ET.SubElement(gpx, "trk")
    trk_name = ET.SubElement(trk, "name")
    trk_name.text = f"{route_name}_Track"
    trkseg = ET.SubElement(trk, "trkseg")

    for coord in route_coords:
        lon, lat = coord[0], coord[1]
        ET.SubElement(trkseg, "trkpt", {"lat": f"{lat:.6f}", "lon": f"{lon:.6f}"})

    return _format_xml(gpx)


def export_to_rtz(
    waypoints: List[Dict[str, Any]],
    draft: float,
    speed_knots: float,
    ukc: float,
    route_name: str = "SeaPath_Passage_Plan",
) -> str:
    """
    Generate RTZ 1.1 (IEC 61174:2015 Annex S) XML format for commercial ECDIS plotters.
    """
    route = ET.Element("route", {
        "xmlns": "http://www.cirm.org/RTZ/1/1",
        "version": "1.1",
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "xsi:schemaLocation": "http://www.cirm.org/RTZ/1/1 RTZ_v1.1.xsd",
    })

    route_info = ET.SubElement(route, "routeInfo", {
        "routeName": route_name,
        "optimizationMethod": "Bathymetric Safety Clearance",
    })
    
    # Vessel operational parameters extension
    vessel_ext = ET.SubElement(route_info, "extensions")
    vessel_data = ET.SubElement(vessel_ext, "vesselDraft")
    vessel_data.text = f"{draft:.2f}"
    vessel_speed = ET.SubElement(vessel_ext, "planSpeed")
    vessel_speed.text = f"{speed_knots:.1f}"

    waypoints_el = ET.SubElement(route, "waypoints")

    for idx, wp in enumerate(waypoints, start=1):
        lat = wp["lat"]
        lon = wp["lon"]
        radius_m = wp.get("turn_radius_m", 150.0)
        radius_nm = radius_m / 1852.0
        rot_val = wp.get("rot_deg_min", 0.0)

        wp_attrs = {
            "id": str(idx),
            "name": f"WP{idx:02d}",
            "radius": f"{radius_nm:.3f}",
        }
        if abs(rot_val) > 0.1:
            wp_attrs["rot"] = f"{abs(rot_val):.1f}"

        wp_el = ET.SubElement(waypoints_el, "waypoint", wp_attrs)

        ET.SubElement(wp_el, "position", {
            "lat": f"{lat:.6f}",
            "lon": f"{lon:.6f}",
        })

        port_xtd = wp.get("port_xtd_nm", 0.10)
        stbd_xtd = wp.get("stbd_xtd_nm", 0.10)

        # Leg specification (IEC 61174 standards)
        leg_el = ET.SubElement(wp_el, "leg", {
            "legType": "Straight",
            "portXTD": f"{port_xtd:.2f}",
            "starboardXTD": f"{stbd_xtd:.2f}",
            "safetyContour": f"{(draft + ukc):.1f}",
        })

    return _format_xml(route)
