"""Turning one GTFS-RT vehicle into the record written to the data file."""

from google.transit import gtfs_realtime_pb2

_VP = gtfs_realtime_pb2.VehiclePosition


def _enum_name(message, field: str, enum) -> str | None:
    return (
        enum.Name(getattr(message, field)) if message.HasField(field) else None
    )


def _field(message, name: str):
    return getattr(message, name) if message.HasField(name) else None


def _enum_fields(vehicle) -> dict:
    # Enums go to disk by name, so the archive reads without the
    # .proto. HasField and not the bare attribute: proto2 returns the
    # default for an unset field, and IN_TRANSIT_TO and SCHEDULED are
    # both defaults and the values ЦГМ actually sends. An enum value
    # these bindings don't know also lands as None (proto2 enums are
    # closed: the parser moves it to unknown fields).
    return {
        'current_status': _enum_name(
            vehicle,
            'current_status',
            _VP.VehicleStopStatus,
        ),
        'occupancy_status': _enum_name(
            vehicle,
            'occupancy_status',
            _VP.OccupancyStatus,
        ),
        'congestion_level': _enum_name(
            vehicle,
            'congestion_level',
            _VP.CongestionLevel,
        ),
        'schedule_relationship': (
            _enum_name(
                vehicle.trip,
                'schedule_relationship',
                gtfs_realtime_pb2.TripDescriptor.ScheduleRelationship,
            )
            if vehicle.HasField('trip')
            else None
        ),
    }


def _core_fields(vehicle, poll_ts: int) -> dict:
    position = vehicle.position
    trip = vehicle.trip if vehicle.HasField('trip') else None
    speed = _field(position, 'speed')
    return {
        'snapshot_ts': poll_ts,
        'vehicle_id': vehicle.vehicle.id
        if vehicle.HasField('vehicle')
        else None,
        'route_id': trip.route_id if trip else None,
        'trip_id': trip.trip_id if trip else None,
        'lat': round(position.latitude, 6),
        'lon': round(position.longitude, 6),
        'bearing': _field(position, 'bearing'),
        'speed_ms': None if speed is None else round(speed, 2),
        'vehicle_ts': _field(vehicle, 'timestamp'),
        'stop_id': _field(vehicle, 'stop_id'),
    }


def vehicle_record(vehicle, poll_ts: int) -> dict:
    """Return the data-file record for `vehicle`, keys in archive order."""
    return {**_core_fields(vehicle, poll_ts), **_enum_fields(vehicle)}
