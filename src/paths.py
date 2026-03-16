"""SignalK path constants for subscription and publishing."""

from __future__ import annotations

from dataclasses import dataclass

# ---------- Subscribed paths (input) ----------

# Wind
WIND_SPEED_TRUE = "environment.wind.speedTrue"
WIND_ANGLE_TRUE_WATER = "environment.wind.angleTrueWater"
WIND_SPEED_APPARENT = "environment.wind.speedApparent"
WIND_ANGLE_APPARENT = "environment.wind.angleApparent"

# Navigation
SPEED_THROUGH_WATER = "navigation.speedThroughWater"
SPEED_OVER_GROUND = "navigation.speedOverGround"
COG_TRUE = "navigation.courseOverGroundTrue"
HEADING_TRUE = "navigation.headingTrue"
RATE_OF_TURN = "navigation.rateOfTurn"
ATTITUDE = "navigation.attitude"
POSITION = "navigation.position"

# Steering
RUDDER_ANGLE = "steering.rudderAngle"

# Environment
CURRENT_DRIFT = "environment.current.drift"
CURRENT_SET_TRUE = "environment.current.setTrue"
WAVE_HEIGHT = "environment.water.waves.significantHeight"
WAVE_PERIOD = "environment.water.waves.truePeriod"
MOTION_SEVERITY = "environment.water.waves.motionSeverity"
DEPTH = "environment.depth.belowKeel"

# Propulsion (optional — may not exist on all vessels)
# These use wildcard subscription; actual paths are like propulsion.port.revolutions
PROPULSION_REVOLUTIONS = "propulsion.*.revolutions"
PROPULSION_STATE = "propulsion.*.state"


@dataclass(frozen=True)
class SubscriptionPath:
    """A SignalK path with its desired subscription period."""

    path: str
    period_ms: int


# All paths we subscribe to, with desired update rates.
SUBSCRIPTIONS: list[SubscriptionPath] = [
    # Wind — 1s
    SubscriptionPath(WIND_SPEED_TRUE, 1000),
    SubscriptionPath(WIND_ANGLE_TRUE_WATER, 1000),
    SubscriptionPath(WIND_SPEED_APPARENT, 1000),
    SubscriptionPath(WIND_ANGLE_APPARENT, 1000),
    # Navigation — 1s
    SubscriptionPath(SPEED_THROUGH_WATER, 1000),
    SubscriptionPath(SPEED_OVER_GROUND, 1000),
    SubscriptionPath(COG_TRUE, 1000),
    SubscriptionPath(HEADING_TRUE, 1000),
    SubscriptionPath(RATE_OF_TURN, 1000),
    SubscriptionPath(ATTITUDE, 1000),
    # Navigation — 5s
    SubscriptionPath(POSITION, 5000),
    # Steering — 1s
    SubscriptionPath(RUDDER_ANGLE, 1000),
    # Environment — 5s
    SubscriptionPath(CURRENT_DRIFT, 5000),
    SubscriptionPath(CURRENT_SET_TRUE, 5000),
    SubscriptionPath(WAVE_HEIGHT, 5000),
    SubscriptionPath(WAVE_PERIOD, 5000),
    SubscriptionPath(MOTION_SEVERITY, 5000),
    SubscriptionPath(DEPTH, 5000),
    # Propulsion — 1s (optional, will silently produce no data if absent)
    SubscriptionPath(PROPULSION_REVOLUTIONS, 1000),
    SubscriptionPath(PROPULSION_STATE, 1000),
]


# ---------- Published paths (output) ----------

PUB_POLAR_SPEED = "performance.polarSpeed"
PUB_POLAR_SPEED_RATIO = "performance.polarSpeedRatio"
PUB_VMG = "performance.velocityMadeGood"
PUB_BEAT_ANGLE = "performance.beatAngle"
PUB_BEAT_ANGLE_VMG = "performance.beatAngleVelocityMadeGood"
PUB_BEAT_ANGLE_TARGET_SPEED = "performance.beatAngleTargetSpeed"
PUB_GYBE_ANGLE = "performance.gybeAngle"
PUB_GYBE_ANGLE_VMG = "performance.gybeAngleVelocityMadeGood"
PUB_GYBE_ANGLE_TARGET_SPEED = "performance.gybeAngleTargetSpeed"
PUB_TARGET_ANGLE = "performance.targetAngle"
PUB_TARGET_SPEED = "performance.targetSpeed"


# Meta information for published paths (sent once on startup).
PUBLISH_META: dict[str, dict] = {
    PUB_POLAR_SPEED: {
        "units": "m/s",
        "description": "Target boat speed from learned polar at current TWS/TWA",
        "displayName": "Polar Speed",
        "shortName": "Pol Spd",
    },
    PUB_POLAR_SPEED_RATIO: {
        "units": "ratio",
        "description": "Actual boat speed / polar target speed",
        "displayName": "Polar Speed Ratio",
        "shortName": "% Polar",
        "displayScale": {"lower": 0, "upper": 1.5},
    },
    PUB_VMG: {
        "units": "m/s",
        "description": "Velocity made good toward/away from wind",
        "displayName": "VMG",
        "shortName": "VMG",
    },
    PUB_BEAT_ANGLE: {
        "units": "rad",
        "description": "Optimal upwind true wind angle at current TWS",
        "displayName": "Beat Angle",
        "shortName": "Beat",
    },
    PUB_BEAT_ANGLE_VMG: {
        "units": "m/s",
        "description": "VMG at optimal beat angle",
        "displayName": "Beat Angle VMG",
        "shortName": "Beat VMG",
    },
    PUB_BEAT_ANGLE_TARGET_SPEED: {
        "units": "m/s",
        "description": "Target boat speed at optimal beat angle",
        "displayName": "Beat Angle Target Speed",
        "shortName": "Beat Tgt",
    },
    PUB_GYBE_ANGLE: {
        "units": "rad",
        "description": "Optimal downwind true wind angle at current TWS",
        "displayName": "Gybe Angle",
        "shortName": "Gybe",
    },
    PUB_GYBE_ANGLE_VMG: {
        "units": "m/s",
        "description": "VMG at optimal gybe angle",
        "displayName": "Gybe Angle VMG",
        "shortName": "Gybe VMG",
    },
    PUB_GYBE_ANGLE_TARGET_SPEED: {
        "units": "m/s",
        "description": "Target boat speed at optimal gybe angle",
        "displayName": "Gybe Angle Target Speed",
        "shortName": "Gybe Tgt",
    },
    PUB_TARGET_ANGLE: {
        "units": "rad",
        "description": "Current applicable target TWA (beat or gybe)",
        "displayName": "Target Angle",
        "shortName": "Tgt Ang",
    },
    PUB_TARGET_SPEED: {
        "units": "m/s",
        "description": "Current applicable target boat speed",
        "displayName": "Target Speed",
        "shortName": "Tgt Spd",
    },
}
