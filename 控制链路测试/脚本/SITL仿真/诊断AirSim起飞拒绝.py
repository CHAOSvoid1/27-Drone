"""Read-only snapshot for diagnosing a PX4/AirSim arming rejection."""

import math
import airsim


client = airsim.MultirotorClient()
client.confirmConnection()

state = client.getMultirotorState(vehicle_name="PX4")
kin = client.simGetGroundTruthKinematics(vehicle_name="PX4")
pose = client.simGetVehiclePose(vehicle_name="PX4")
collision = client.simGetCollisionInfo(vehicle_name="PX4")
actor_scale = client.simGetObjectScale("PX4")
gps = client.getGpsData(vehicle_name="PX4")
baro = client.getBarometerData(vehicle_name="PX4")
imu = client.getImuData(vehicle_name="PX4")


def xyz(value):
    return (value.x_val, value.y_val, value.z_val)


def finite(values):
    return all(math.isfinite(float(value)) for value in values)


position = xyz(kin.position)
velocity = xyz(kin.linear_velocity)
acceleration = xyz(kin.linear_acceleration)
gps_values = (
    gps.gnss.geo_point.latitude,
    gps.gnss.geo_point.longitude,
    gps.gnss.geo_point.altitude,
)

print("landed_state=", state.landed_state)
print("ready=", state.ready)
print("can_arm=", state.can_arm)
print("ground_truth_position_ned_m=", position, "finite=", finite(position))
print("ground_truth_velocity_mps=", velocity, "finite=", finite(velocity))
print("ground_truth_acceleration_mps2=", acceleration, "finite=", finite(acceleration))
print("vehicle_pose_position_ned_m=", xyz(pose.position))
print("vehicle_actor_scale=", xyz(actor_scale))
print("collision_has_collided=", collision.has_collided)
print("collision_object=", collision.object_name)
print("collision_penetration_depth_m=", collision.penetration_depth)
print("collision_position_ned_m=", xyz(collision.position))
print("collision_impact_point_ned_m=", xyz(collision.impact_point))
print("collision_normal_ned=", xyz(collision.normal))
print("gps_fix_type=", gps.gnss.fix_type)
print("gps_lat_lon_alt=", gps_values, "finite=", finite(gps_values))
print("gps_eph_epv=", gps.gnss.eph, gps.gnss.epv)
print("barometer_altitude_m=", baro.altitude, "finite=", math.isfinite(baro.altitude))
print("imu_angular_velocity=", xyz(imu.angular_velocity))
print("imu_linear_acceleration=", xyz(imu.linear_acceleration))
