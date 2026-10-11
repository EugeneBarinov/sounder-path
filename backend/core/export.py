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
    safety_contour: Optional[float] = None,
    safety_status: Optional[str] = None,
    is_safe: Optional[bool] = None,
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
    contour_str = f" | Safety Contour: {safety_contour:.2f}m" if safety_contour is not None else ""
    warning_str = " [CRITICAL WARNING: HAZARD DETECTED]" if (is_safe is False or safety_status == "CRITICAL_HAZARD") else ""
    desc_el.text = f"Draft: {draft:.1f}m | Speed: {speed_knots:.1f} kts | Required UKC: {ukc:.1f}m{contour_str}{warning_str}"

    # Route with navigational waypoints
    rte = ET.SubElement(gpx, "rte")
    rte_name = ET.SubElement(rte, "name")
    rte_name.text = route_name

    for idx, wp in enumerate(waypoints, start=1):
        lat = float(wp.get("lat") if wp.get("lat") is not None else wp.get("latitude", 0.0))
        lon = float(wp.get("lon") if wp.get("lon") is not None else (wp.get("lng") if wp.get("lng") is not None else wp.get("longitude", 0.0)))
        rtept = ET.SubElement(rte, "rtept", {"lat": f"{lat:.6f}", "lon": f"{lon:.6f}"})
        
        pt_name = ET.SubElement(rtept, "name")
        pt_name.text = f"WP{idx:02d}"

        brg = wp.get("leg_bearing_deg", 0.0)
        dist = wp.get("leg_distance_nm", 0.0)
        depth = (
            wp.get("depth_m")
            if wp.get("depth_m") is not None
            else (
                wp.get("depth_at_waypoint")
                if wp.get("depth_at_waypoint") is not None
                else wp.get("depth", 0.0)
            )
        )
        ukc_val = (
            wp.get("clearance_m")
            if wp.get("clearance_m") is not None
            else wp.get("clearance", 0.0)
        )
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
        if isinstance(coord, (list, tuple)) and len(coord) >= 2:
            lon, lat = float(coord[0]), float(coord[1])
            ET.SubElement(trkseg, "trkpt", {"lat": f"{lat:.6f}", "lon": f"{lon:.6f}"})
        elif isinstance(coord, dict):
            c_lat = float(coord.get("lat") if coord.get("lat") is not None else coord.get("latitude", 0.0))
            c_lon = float(coord.get("lon") if coord.get("lon") is not None else (coord.get("lng") if coord.get("lng") is not None else coord.get("longitude", 0.0)))
            ET.SubElement(trkseg, "trkpt", {"lat": f"{c_lat:.6f}", "lon": f"{c_lon:.6f}"})

    return _format_xml(gpx)


def export_to_rtz(
    waypoints: List[Dict[str, Any]],
    draft: float,
    speed_knots: float,
    ukc: float,
    route_name: str = "SeaPath_Passage_Plan",
    safety_contour: Optional[float] = None,
    safety_status: Optional[str] = None,
    is_safe: Optional[bool] = None,
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

    is_hazardous = (is_safe is False or safety_status == "CRITICAL_HAZARD")
    route_info_attrs = {
        "routeName": route_name,
        "optimizationMethod": "Bathymetric Safety Clearance",
    }
    if is_hazardous:
        route_info_attrs["routeStatus"] = "UNVERIFIED_HAZARD"

    route_info = ET.SubElement(route, "routeInfo", route_info_attrs)

    if is_hazardous:
        notes_el = ET.SubElement(route_info, "routeNotes")
        notes_el.text = "CRITICAL WARNING: Route contains unresolved navigational hazards or insufficient UKC."
    
    # Vessel operational parameters extension
    vessel_ext = ET.SubElement(route_info, "extensions")
    vessel_data = ET.SubElement(vessel_ext, "vesselDraft")
    vessel_data.text = f"{draft:.2f}"
    vessel_speed = ET.SubElement(vessel_ext, "planSpeed")
    vessel_speed.text = f"{speed_knots:.1f}"

    waypoints_el = ET.SubElement(route, "waypoints")

    for idx, wp in enumerate(waypoints, start=1):
        lat = float(wp.get("lat") if wp.get("lat") is not None else wp.get("latitude", 0.0))
        lon = float(wp.get("lon") if wp.get("lon") is not None else (wp.get("lng") if wp.get("lng") is not None else wp.get("longitude", 0.0)))
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

        contour_val = safety_contour if safety_contour is not None else (draft + ukc)

        # Leg specification (IEC 61174 standards)
        leg_el = ET.SubElement(wp_el, "leg", {
            "legType": "Straight",
            "portXTD": f"{port_xtd:.2f}",
            "starboardXTD": f"{stbd_xtd:.2f}",
            "safetyContour": f"{contour_val:.1f}",
        })

    return _format_xml(route)
