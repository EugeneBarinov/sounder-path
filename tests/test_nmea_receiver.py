"""
Unit tests for NMEA 0183 Marine Telemetry Ingestion Engine
==========================================================
Tests:
- Checksum verification (valid, invalid, absent)
- GNSS coordinate decoding ($GPGGA, $GPRMC)
- Depth sounder decoding ($SDDPT with offset, $SDDBT)
- Ground speed and course over ground ($GPVTG, $GPRMC)
- Environmental water temperature ($VWMTW)
- Heading ($HCHDT)
- Sensor timeout and loss detection
- Callback notification dispatch
"""

import pytest
import time
from backend.core.nmea_receiver import (
    verify_nmea_checksum,
    parse_nmea_lat,
    parse_nmea_lon,
    NmeaReceiver,
    VesselLiveState,
)


def test_verify_nmea_checksum():
    # Valid NMEA sentences with standard checksums
    valid_gga = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"
    assert verify_nmea_checksum(valid_gga) is True

    valid_rmc = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
    assert verify_nmea_checksum(valid_rmc) is True

    valid_dpt = "$SDDPT,14.2,-0.5*48"
    assert verify_nmea_checksum(valid_dpt) is True

    # Corrupted checksum
    corrupted_gga = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*FF"
    assert verify_nmea_checksum(corrupted_gga) is False

    # Checksum omitted (allowed in relaxed mode)
    no_cs = "$SDDPT,12.5,0.0"
    assert verify_nmea_checksum(no_cs) is True

    # Empty / garbage
    assert verify_nmea_checksum("") is False
    assert verify_nmea_checksum("GARBAGE_NO_DELIMITER") is False


def test_parse_nmea_coordinates():
    # 44°37.200' N -> 44.6200000°
    lat_n = parse_nmea_lat("4437.200", "N")
    assert lat_n is not None
    assert pytest.approx(lat_n, 1e-5) == 44.620000

    # 44°37.200' S -> -44.6200000°
    lat_s = parse_nmea_lat("4437.200", "S")
    assert lat_s is not None
    assert pytest.approx(lat_s, 1e-5) == -44.620000

    # 033°31.800' E -> 33.5300000°
    lon_e = parse_nmea_lon("03331.800", "E")
    assert lon_e is not None
    assert pytest.approx(lon_e, 1e-5) == 33.530000

    # 033°31.800' W -> -33.5300000°
    lon_w = parse_nmea_lon("03331.800", "W")
    assert lon_w is not None
    assert pytest.approx(lon_w, 1e-5) == -33.530000

    # Invalid coordinates
    assert parse_nmea_lat("", "N") is None
    assert parse_nmea_lat("12", "N") is None
    assert parse_nmea_lon("", "E") is None


def test_nmea_receiver_gga_parsing():
    receiver = NmeaReceiver()
    gga = "$GPGGA,092750.000,4437.200,N,03331.800,E,2,10,0.85,12.4,M,0.0,M,,*72"
    
    # Calculate correct checksum if needed
    # Let's test feed with auto-computed checksum or directly
    body = "GPGGA,092750.000,4437.200,N,03331.800,E,2,10,0.85,12.4,M,0.0,M,,"
    cs = 0
    for c in body:
        cs ^= ord(c)
    sentence = f"${body}*{cs:02X}"

    success = receiver.feed_sentence(sentence)
    assert success is True
    st = receiver.state

    assert st.gps_valid is True
    assert pytest.approx(st.lat, 1e-4) == 44.6200
    assert pytest.approx(st.lon, 1e-4) == 33.5300
    assert st.fix_quality == 2
    assert st.fix_status == "DGPS"
    assert st.satellites_count == 10
    assert pytest.approx(st.hdop, 0.01) == 0.85
    assert pytest.approx(st.altitude_m, 0.1) == 12.4


def test_nmea_receiver_rmc_and_depth():
    receiver = NmeaReceiver()

    # Feed RMC for course & speed
    body_rmc = "GPRMC,092751.000,A,4437.210,N,03331.815,E,007.8,045.2,261026,,,A"
    cs_rmc = 0
    for c in body_rmc:
        cs_rmc ^= ord(c)
    sentence_rmc = f"${body_rmc}*{cs_rmc:02X}"

    assert receiver.feed_sentence(sentence_rmc) is True
    st = receiver.state
    assert pytest.approx(st.sog_knots, 0.1) == 7.8
    assert pytest.approx(st.cog_deg, 0.1) == 45.2

    # Feed Depth ($SDDPT)
    body_dpt = "SDDPT,008.45,-0.30"
    cs_dpt = 0
    for c in body_dpt:
        cs_dpt ^= ord(c)
    sentence_dpt = f"${body_dpt}*{cs_dpt:02X}"

    assert receiver.feed_sentence(sentence_dpt) is True
    assert st.depth_valid is True
    # 8.45 - 0.30 = 8.15m
    assert pytest.approx(st.depth_m, 0.01) == 8.15
    assert pytest.approx(st.depth_offset_m, 0.01) == -0.30


def test_nmea_receiver_water_temp_and_heading():
    receiver = NmeaReceiver()

    # Temperature
    body_mtw = "VWMTW,16.8,C"
    cs_mtw = 0
    for c in body_mtw:
        cs_mtw ^= ord(c)
    sentence_mtw = f"${body_mtw}*{cs_mtw:02X}"
    assert receiver.feed_sentence(sentence_mtw) is True
    assert pytest.approx(receiver.state.water_temp_c, 0.1) == 16.8

    # Heading True
    body_hdt = "HCHDT,044.8,T"
    cs_hdt = 0
    for c in body_hdt:
        cs_hdt ^= ord(c)
    sentence_hdt = f"${body_hdt}*{cs_hdt:02X}"
    assert receiver.feed_sentence(sentence_hdt) is True
    assert pytest.approx(receiver.state.heading_deg, 0.1) == 44.8


def test_nmea_receiver_callbacks_and_timeout():
    receiver = NmeaReceiver(gps_timeout_sec=0.1, depth_timeout_sec=0.1)
    received_states = []

    def on_telemetry(state: VesselLiveState):
        received_states.append(state.depth_m)

    receiver.add_listener(on_telemetry)

    # Send depth
    body = "SDDPT,12.0,0.0"
    cs = 0
    for c in body:
        cs ^= ord(c)
    receiver.feed_sentence(f"${body}*{cs:02X}")

    assert len(received_states) == 1
    assert received_states[0] == 12.0
    assert receiver.state.depth_valid is True

    # Wait for sensor timeout
    time.sleep(0.15)
    receiver.check_timeouts()
    assert receiver.state.depth_valid is False


@pytest.mark.anyio
async def test_nmea_udp_network_reception():
    import socket
    from backend.core.nmea_receiver import NmeaManager

    mgr = NmeaManager()
    # Bind to random loopback high port
    test_port = 19110
    success = await mgr.start_udp_listener(host="127.0.0.1", port=test_port)
    assert success is True
    assert mgr.is_running is True

    try:
        # Send raw UDP packet with NMEA sentences
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        body = "SDDPT,015.60,0.0"
        cs = 0
        for c in body:
            cs ^= ord(c)
        packet = f"${body}*{cs:02X}\r\n".encode("ascii")
        sock.sendto(packet, ("127.0.0.1", test_port))
        sock.close()

        # Allow event loop to process UDP datagram
        import asyncio
        for _ in range(10):
            await asyncio.sleep(0.05)
            if mgr.receiver.state.depth_m is not None:
                break

        assert mgr.receiver.state.depth_m == 15.6
        assert mgr.receiver.state.depth_valid is True
    finally:
        await mgr.stop()
        assert mgr.is_running is False
