import gym
from gym import error, spaces
import airsim
import numpy as np
import math
import time
from PIL import Image

class DroneAirsim(gym.Env):
	metadata = {'render.modes': ['human']}
	reward_range = (-float(100),float(100))

	def __init__(self, hover_height=-1.2, ip='10.2.36.125',image_shape=[512,512]):
		self.z = hover_height
		self.server_address = ip

		self.client = airsim.MultirotorClient(ip=self.server_address)
		self.client.confirmConnection()
		self.client.enableApiControl(True)
		self.client.armDisarm(True)

		"""Goal Pose requires a static mesh object named "goal" in Unreal Environment/Scene  """
		self.start_pose=self.getPose()
		self.goal = [self.client.simGetObjectPose("goal").position.x_val,self.client.simGetObjectPose("goal").position.y_val]
		self.prev_pose = self.start_pose
		self.goal_distance=np.sqrt(np.power((self.goal[0]-self.start_pose[0]),2) + np.power((self.goal[1]-self.start_pose[1]),2))

		self.client.moveToPositionAsync(self.start_pose[0], self.start_pose[1], self.z, 1).join()
		print(" Starting Position:{}, Distance to Goal:{}".format(self.start_pose,self.goal_distance))

		self.action_space=spaces.Discrete(3)

		"""Parameters for action and state"""
		self.action_duration=1
		self.yaw_degrees=20
		self.num_frames=5								# Stack 5 frames
		self.img_shape=image_shape

	"""Helper functions for actions """

	def straight(self,speed):
		pitch, roll, yaw = airsim.to_eularian_angles(self.client.simGetVehiclePose().orientation)
		dx = math.cos(yaw) * speed
		dy = math.sin(yaw) * speed
		x = self.client.simGetVehiclePose().position.x_val
		y = self.client.simGetVehiclePose().position.y_val

		"""client.moveByVelocityAsync() causes z position to dip as well due to lack of PID control"""
		self.client.moveToPositionAsync(x + dx, y + dy, self.z,speed,airsim.DrivetrainType.ForwardOnly)
		init_time=time.time()
		return init_time

	def yaw_right(self):
		self.client.rotateByYawRateAsync(self.yaw_degrees, self.action_duration)
		init_time = time.time()
		return init_time

	def yaw_left(self):

		self.client.rotateByYawRateAsync(-self.yaw_degrees, self.action_duration)
		init_time = time.time()
		return init_time

	def take_action(self, action):

		collided = False
		frame_buffer=[]											# Collects all frames captured while doing action

		if action == 0:
			# Move in direction of yaw heading with 1m/s for 1s
			start=self.straight(1)

			while self.action_duration > time.time() - start:
				if self.client.simGetCollisionInfo().has_collided == True:
					collided=True
				frame_buffer.append(self.client.simGetImages([airsim.ImageRequest("0", airsim.ImageType.Scene,False,False)])[0])

			self.client.moveByVelocityZAsync(0, 0, self.z,1).join()

		if action == 1:
			# Rotate right on z axis for 1 sec
			start = self.yaw_right()

			while self.action_duration > time.time() - start:
				if self.client.simGetCollisionInfo().has_collided == True:
					collided=True
				frame_buffer.append(self.client.simGetImages([airsim.ImageRequest("0", airsim.ImageType.Scene,False,False)])[0])

			self.client.moveByVelocityZAsync(0, 0, self.z, 0.5).join()
			self.client.rotateByYawRateAsync(0,0.5).join()

		if action == 2:
			# Rotate left on z axis  for 1s
			start= self.yaw_left()

			while self.action_duration > time.time() - start:
				if self.client.simGetCollisionInfo().has_collided == True:
					collided=True
				frame_buffer.append(self.client.simGetImages([airsim.ImageRequest("0", airsim.ImageType.Scene,False,False)])[0])

			self.client.moveByVelocityZAsync(0, 0, self.z,0.5).join()
			self.client.rotateByYawRateAsync(0, 0.5).join()

		return frame_buffer,collided

	def process_frame(self,response):

		frame = airsim.string_to_uint8_array(response.image_data_uint8).reshape(self.img_shape[0],self.img_shape[1], 3)
		frame = Image.fromarray(frame).convert('L')
		frame = np.asarray(frame)
		return frame


	def stackFrames(self,*args,init_state=False):

		if init_state == True:
			response = self.client.simGetImages([
				airsim.ImageRequest("0", airsim.ImageType.Scene,False,False)
			])[0]

			assert response.height == self.img_shape[0] and response.width == self.img_shape[1], "Input Image size from airsim settings.json and env doesn't match"

			frame=self.process_frame(response)
			stack_frames=[frame for i in range(self.num_frames)]
			stack_frames=np.array(stack_frames)
			return stack_frames

		else:
			responses_in=args[0]
			len_frames=len(responses_in)
			assert len_frames >= self.num_frames+2, "Frame rate not enough"

			"""
			0, N/2-2, N/2, N/2+2, N-1 frames are stacked together to get state
			Modify these indexes if frame rate is less(say 5-7 fps)
			"""

			indexes=[0,int(len_frames/2)-2,int(len_frames/2),int(len_frames/2)+2,len_frames-1]
			stack_frames=[self.process_frame(responses_in[i]) for i in indexes]
			stack_frames=np.array(stack_frames)
			return stack_frames


	def step(self, action):

		frames,collided=self.take_action(action)
		new_pose=self.getPose()
		reward,done = self.get_reward(collided,new_pose)
		self.prev_pose=new_pose
		new_state=self.stackFrames(frames)
		return new_state,reward,done,{}


	def get_reward(self,collision,new_pose):

		eps_end=False

		if not collision:
			prev_dist=np.sqrt(np.power((self.goal[0]-self.prev_pose[0]),2) + np.power((self.goal[1]-self.prev_pose[1]),2))
			new_dist=np.sqrt(np.power((self.goal[0]-new_pose[0]),2) + np.power((self.goal[1]-new_pose[1]),2))

			if new_dist < 3:
				eps_end=True
				reward=100
			else:
				eps_end=False
				reward=-1+(prev_dist-new_dist)
		else:
			eps_end=True
			reward=-100

		return reward,eps_end

	def getPose(self):
		return [self.client.simGetVehiclePose().position.x_val,self.client.simGetVehiclePose().position.y_val]


	def reset(self):

		self.client.reset()
		self.client.enableApiControl(True)
		self.client.armDisarm(True)
		self.client.moveToPositionAsync(self.start_pose[0],self.start_pose[1],self.z,1).join()
		self.prev_pose=self.start_pose
		return self.stackFrames(init_state=True)

	def render(self, mode='human'):
		raise NotImplementedError

	def close(self):
		print("Shutting down environment...")
		self.client.armDisarm(False)
		self.client.reset()
		self.client.enableApiControl(False)