"""
Copyright 2018-2019 Amazon.com, Inc. or its affiliates. All Rights Reserved.
Copyright 2019-2020 AWS DeepRacer Community. All Rights Reserved.

Permission is hereby granted, free of charge, to any person obtaining a copy of this
software and associated documentation files (the "Software"), to deal in the Software
without restriction, including without limitation the rights to use, copy, modify,
merge, publish, distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""

from datetime import datetime
from decimal import Decimal

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.collections import PatchCollection
from matplotlib.patches import Rectangle
from shapely.geometry.polygon import LineString

from .cw_utils import CloudWatchLogs as cw


class SimulationLogsIO:
    """ Utilities for loading the logs
    """

    @staticmethod
    def load_single_file(fname, data=None):
        """Loads a single log file and remembers only the SIM_TRACE_LOG lines

        Arguments:
        fname - path to the file
        data - list to populate with SIM_TRACE_LOG lines. Default: None

        Returns:
        List of loaded log lines. If data is not None, it is the reference returned
        and the list referenced has new log lines appended
        """
        if not data:
            data = []

        with open(fname, 'r') as f:
            for line in f.readlines():
                if "SIM_TRACE_LOG" in line:
                    parts = line.split("SIM_TRACE_LOG:")[1].split('\t')[0].split(",")
                    data.append(",".join(parts))

        return data

    @staticmethod
    def load_data(fname):
        """Load all log files for a given simulation

        Looks for all files for a given simulation and loads them. Takes the local training
        into account where in some cases the logs are split when they reach a certain size,
        and given a suffix .1, .2 etc.

        Arguments:
        fname - path to the file

        Returns:
        List of loaded log lines
        """
        from os.path import isfile
        data = []

        i = 1

        while isfile('%s.%s' % (fname, i)):
            SimulationLogsIO.load_single_file('%s.%s' % (fname, i), data)
            i += 1

        SimulationLogsIO.load_single_file(fname, data)

        if i > 1:
            print("Loaded %s log files (logs rolled over)" % i)

        return data

    @staticmethod
    def convert_to_pandas(data, episodes_per_iteration=20):
        """Load the log data to pandas dataframe
        stdout_ = 'SIM_TRACE_LOG:%d,%d,%.4f,%.4f,%.4f,%.2f,%.2f,%d,%.4f,%s,%s,%.4f,%d,%.2f,%s\n' % (
                self.episodes, self.steps, model_location[0], model_location[1], model_heading,
                self.steering_angle,
                self.speed,
                self.action_taken,
                self.reward,
                self.done,
                all_wheels_on_track,
                current_progress,
                closest_waypoint_index,
                self.track_length,
                time.time())
            print(stdout_)
        """

        df_list = list()

        # ignore the first two dummy values that coach throws at the start.
        for d in data[2:]:
            parts = d.rstrip().split(",")
            episode = int(parts[0])
            steps = int(parts[1])
            x = 100 * float(parts[2])
            y = 100 * float(parts[3])
            yaw = float(parts[4])
            steer = float(parts[5])
            throttle = float(parts[6])
            action = float(parts[7])
            reward = float(parts[8])
            done = 0 if 'False' in parts[9] else 1
            all_wheels_on_track = parts[10]
            progress = float(parts[11])
            closest_waypoint = int(parts[12])
            track_len = float(parts[13])
            tstamp = Decimal(parts[14])

            iteration = int(episode / episodes_per_iteration) + 1
            df_list.append((iteration, episode, steps, x, y, yaw, steer, throttle,
                            action, reward, done, all_wheels_on_track, progress,
                            closest_waypoint, track_len, tstamp))

        header = ['iteration', 'episode', 'steps', 'x', 'y', 'yaw', 'steer',
                  'throttle', 'action', 'reward', 'done', 'on_track', 'progress',
                  'closest_waypoint', 'track_len', 'timestamp']

        df = pd.DataFrame(df_list, columns=header)
        return df

    @staticmethod
    def load_to_pandas(eval_fname):
        eval_data = SimulationLogsIO.load_data(eval_fname)
        return SimulationLogsIO.convert_to_pandas(eval_data)

    @staticmethod
    def load_eval_logs(logs):
        full_dataframe = None
        for log in logs:
            dataframe = SimulationLogsIO.load_to_pandas(log[0])
            dataframe['stream'] = log[1]
            if full_dataframe is not None:
                full_dataframe = full_dataframe.append(dataframe)
            else:
                full_dataframe = dataframe

        return full_dataframe.sort_values(
            ['stream', 'episode', 'steps']).reset_index()

    @staticmethod
    def normalize_rewards(df):
        # Normalize the rewards to a 0-1 scale
        from sklearn.preprocessing import MinMaxScaler

        min_max_scaler = MinMaxScaler()
        scaled_vals = min_max_scaler.fit_transform(
            df['reward'].values.reshape(df['reward'].values.shape[0], 1))
        df['reward'] = pd.DataFrame(scaled_vals.squeeze())


class AnalysisUtils:
    @staticmethod
    def simulation_agg(panda, firstgroup='iteration', add_timestamp=False, is_eval=False):
        grouped = panda.groupby([firstgroup, 'episode'])

        by_steps = grouped['steps'].agg(np.max).reset_index()
        by_start = grouped.first()['closest_waypoint'].reset_index() \
            .rename(index=str, columns={"closest_waypoint": "start_at"})
        by_progress = grouped['progress'].agg(np.max).reset_index()
        by_throttle = grouped['throttle'].agg(np.mean).reset_index()
        by_time = grouped['timestamp'].agg(np.ptp).reset_index() \
            .rename(index=str, columns={"timestamp": "time"})
        by_time['time'] = by_time['time'].astype(float)

        result = by_steps \
            .merge(by_start) \
            .merge(by_progress, on=[firstgroup, 'episode']) \
            .merge(by_time, on=[firstgroup, 'episode'])

        if not is_eval:
            if 'new_reward' not in panda.columns:
                print('new reward not found, using reward as its values')
                panda['new_reward'] = panda['reward']
            by_new_reward = grouped['new_reward'].agg(np.sum).reset_index()
            result = result.merge(by_new_reward, on=[firstgroup, 'episode'])

        result = result.merge(by_throttle, on=[firstgroup, 'episode'])

        if not is_eval:
            by_reward = grouped['reward'].agg(np.sum).reset_index()
            result = result.merge(by_reward, on=[firstgroup, 'episode'])

        result['time_if_complete'] = result['time'] * 100 / result['progress']

        if not is_eval:
            result['reward_if_complete'] = result['reward'] * 100 / result['progress']
            result['quintile'] = pd.cut(result['episode'], 5, labels=[
                                        '1st', '2nd', '3rd', '4th', '5th'])

        if add_timestamp:
            by_timestamp = grouped['timestamp'].agg(np.max).astype(float).reset_index()
            by_timestamp['timestamp'] = pd.to_datetime(by_timestamp['timestamp'], unit='s')
            result = result.merge(by_timestamp, on=[firstgroup, 'episode'])

        return result

    @staticmethod
    def scatter_aggregates(aggregate_df, title=None, is_eval=False):
        fig, axes = plt.subplots(nrows=2 if is_eval else 3,
                                 ncols=2 if is_eval else 3, figsize=[15, 11])
        if title:
            fig.suptitle(title)
        if not is_eval:
            aggregate_df.plot.scatter('time', 'reward', ax=axes[0, 2])
            aggregate_df.plot.scatter('time', 'new_reward', ax=axes[1, 2])
            aggregate_df.plot.scatter('start_at', 'reward', ax=axes[2, 2])
            aggregate_df.plot.scatter('start_at', 'progress', ax=axes[2, 0])
            aggregate_df.plot.scatter('start_at', 'time_if_complete', ax=axes[2, 1])
        aggregate_df.plot.scatter('time', 'progress', ax=axes[0, 0])
        aggregate_df.hist(column=['time'], bins=20, ax=axes[1, 0])
        aggregate_df.plot.scatter('time', 'steps', ax=axes[0, 1])
        aggregate_df.hist(column=['progress'], bins=20, ax=axes[1, 1])

        plt.show()
        plt.clf()

    @staticmethod
    def scatter_by_groups(panda, group_category='quintile', groupcount=5, title=None):
        grouped = panda.groupby(group_category)

        fig, axes = plt.subplots(nrows=groupcount, ncols=4, figsize=[15, 15])

        if title:
            fig.suptitle(title)

        row = 0
        for name, group in grouped:
            group.plot.scatter('time', 'reward', ax=axes[row, 0])
            group.plot.scatter('time', 'new_reward', ax=axes[row, 1])
            group.hist(column=['time'], bins=20, ax=axes[row, 2])
            axes[row, 3].set(xlim=(0, 100))
            group.hist(column=['progress'], bins=20, ax=axes[row, 3])
            row += 1

        plt.show()
        plt.clf()

    @staticmethod
    def analyze_training_progress(aggregates, title=None):
        aggregates['complete'] = np.where(aggregates['progress'] == 100, 1, 0)

        grouped = aggregates.groupby('iteration')

        reward_per_iteration = grouped['reward'].agg([np.mean, np.std]).reset_index()
        time_per_iteration = grouped['time'].agg([np.mean, np.std]).reset_index()
        progress_per_iteration = grouped['progress'].agg([np.mean, np.std]).reset_index()

        complete_laps = aggregates[aggregates['progress'] == 100.0]
        complete_grouped = complete_laps.groupby('iteration')

        complete_times = complete_grouped['time'].agg([np.mean, np.min, np.max]).reset_index()

        total_completion_rate = complete_laps.shape[0] / aggregates.shape[0]

        complete_per_iteration = grouped['complete'].agg([np.mean]).reset_index()

        print('Number of episodes = ', np.max(aggregates['episode']))
        print('Number of iterations = ', np.max(aggregates['iteration']))

        fig, axes = plt.subplots(nrows=3, ncols=3, figsize=[15, 15])

        if title:
            fig.suptitle(title)

        AnalysisUtils.plot(axes[0, 0], reward_per_iteration, 'iteration', 'Iteration',
                           'mean', 'Mean reward', 'Rewards per Iteration')
        AnalysisUtils.plot(axes[1, 0], reward_per_iteration, 'iteration',
                           'Iteration', 'std', 'Std dev of reward', 'Dev of reward')
        AnalysisUtils.plot(axes[2, 0], aggregates, 'episode', 'Episode', 'reward', 'Total reward')

        AnalysisUtils.plot(axes[0, 1], time_per_iteration, 'iteration',
                           'Iteration', 'mean', 'Mean time', 'Times per Iteration')
        AnalysisUtils.plot(axes[1, 1], time_per_iteration, 'iteration',
                           'Iteration', 'std', 'Std dev of time', 'Dev of time')
        if complete_times.shape[0] > 0:
            AnalysisUtils.plot(axes[2, 1], complete_times, 'iteration', 'Iteration',
                               'mean', 'Time', 'Mean completed laps time')

        AnalysisUtils.plot(axes[0, 2], progress_per_iteration, 'iteration', 'Iteration', 'mean',
                           'Mean progress', 'Progress per Iteration')
        AnalysisUtils.plot(axes[1, 2], progress_per_iteration, 'iteration',
                           'Iteration', 'std', 'Std dev of progress', 'Dev of progress')
        AnalysisUtils.plot(axes[2, 2], complete_per_iteration, 'iteration', 'Iteration', 'mean',
                           'Completion rate', 'Completion rate (avg: %s)' % total_completion_rate)

        plt.show()
        plt.clf()

    @staticmethod
    def plot(ax, df, xval, xlabel, yval, ylabel, title=None):
        df.plot.scatter(xval, yval, ax=ax, s=5, alpha=0.7)
        if title:
            ax.set_title(title)
        ax.set_ylabel(ylabel)
        ax.set_xlabel(xlabel)

        plt.grid(True)


class PlottingUtils:
    @staticmethod
    def plot_coords(ax, ob):
        x, y = ob.xy
        ax.plot(x, y, '.', color='#999999', zorder=1)

    @staticmethod
    def plot_bounds(ax, ob):
        x, y = zip(*list((p.x, p.y) for p in ob.boundary))
        ax.plot(x, y, '.', color='#000000', zorder=1)

    @staticmethod
    def plot_line(ax, ob, color='cyan'):
        x, y = ob.xy
        ax.plot(x, y, color=color, alpha=0.7, linewidth=3, solid_capstyle='round',
                zorder=2)

    @staticmethod
    def print_border(ax, waypoints, inner_border_waypoints, outer_border_waypoints,
                     color='lightgrey'):
        line = LineString(waypoints)
        PlottingUtils.plot_coords(ax, line)
        PlottingUtils.plot_line(ax, line, color)

        line = LineString(inner_border_waypoints)
        PlottingUtils.plot_coords(ax, line)
        PlottingUtils.plot_line(ax, line, color)

        line = LineString(outer_border_waypoints)
        PlottingUtils.plot_coords(ax, line)
        PlottingUtils.plot_line(ax, line, color)

    @staticmethod
    def plot_top_laps(sorted_idx, episode_map, center_line, inner_border,
                      outer_border, n_laps=5):
        fig = plt.figure(n_laps, figsize=(12, n_laps * 10))
        for i in range(n_laps):
            idx = sorted_idx[i]

            episode_data = episode_map[idx]

            ax = fig.add_subplot(n_laps, 1, i + 1)

            line = LineString(center_line)
            PlottingUtils.plot_coords(ax, line)
            PlottingUtils.plot_line(ax, line)

            line = LineString(inner_border)
            PlottingUtils.plot_coords(ax, line)
            PlottingUtils.plot_line(ax, line)

            line = LineString(outer_border)
            PlottingUtils.plot_coords(ax, line)
            PlottingUtils.plot_line(ax, line)

            for idx in range(1, len(episode_data) - 1):
                x1, y1, action, reward, angle, speed = episode_data[idx]
                car_x2, car_y2 = x1 - 0.02, y1
                plt.plot([x1 * 100, car_x2 * 100], [y1 * 100, car_y2 * 100], 'b.')

        plt.show()
        plt.clf()

        return fig

    @staticmethod
    def plot_evaluations(evaluations, inner, outer, graphed_value='throttle'):
        streams = evaluations.sort_values(
            'timestamp', ascending=False).groupby('stream', sort=False)

        for name, stream in streams:
            fig, axes = plt.subplots(2, 3, figsize=(20, 10))
            fig.tight_layout(pad=0.4, w_pad=0.5, h_pad=7.0)

            for id, episode in stream.groupby('episode'):
                PlottingUtils.plot_grid_world(
                    episode, inner, outer, graphed_value, ax=axes[int(id / 3), id % 3])

            plt.show()
            plt.clf()

    @staticmethod
    def plot_grid_world(
        episode_df,
        inner,
        outer,
        graphed_value='throttle',
        min_progress=None,
        ax=None
    ):
        """
        plot a scaled version of lap, along with throttle taken a each position
        """

        episode_df.loc[:, 'distance_diff'] = ((episode_df['x'].shift(1) - episode_df['x']) ** 2 + (
            episode_df['y'].shift(1) - episode_df['y']) ** 2) ** 0.5

        distance = np.nansum(episode_df['distance_diff']) / 100
        lap_time = np.ptp(episode_df['timestamp'].astype(float))
        velocity = distance / lap_time
        average_throttle = np.nanmean(episode_df['throttle'])
        progress = np.nanmax(episode_df['progress'])

        if not min_progress or progress > min_progress:

            distance_lap_time = 'Distance, progress, lap time = %.2f m, %.2f %%, %.2f s' % (
                distance, progress, lap_time
            )
            throttle_velocity = 'Average throttle, velocity = %.2f (Gazebo), %.2f m/s' % (
                average_throttle, velocity
            )

            fig = None
            if ax is None:
                fig = plt.figure(figsize=(16, 10))
                ax = fig.add_subplot(1, 1, 1)

            ax.set_facecolor('midnightblue')

            line = LineString(inner)
            PlottingUtils.plot_coords(ax, line)
            PlottingUtils.plot_line(ax, line)

            line = LineString(outer)
            PlottingUtils.plot_coords(ax, line)
            PlottingUtils.plot_line(ax, line)

            episode_df.plot.scatter('x', 'y', ax=ax, s=3, c=graphed_value,
                                    cmap=plt.get_cmap('plasma'))

            subtitle = '%s%s\n%s\n%s' % (
                ('Stream: %s, ' % episode_df['stream'].iloc[0]
                 ) if 'stream' in episode_df.columns else '',
                datetime.fromtimestamp(episode_df['timestamp'].iloc[0]),
                distance_lap_time,
                throttle_velocity)
            ax.set_title(subtitle)

            if fig:
                plt.show()
                plt.clf()

    @staticmethod
    def plot_track(df, center_line, inner_border, outer_border,
                   track_size=(500, 800), x_shift=0, y_shift=0):
        track = np.zeros(track_size)  # lets magnify the track by *100
        for index, row in df.iterrows():
            x = int(row["x"]) + x_shift
            y = int(row["y"]) + y_shift
            reward = row["reward"]

            # clip values that are off track
            if y >= track_size[0]:
                y = track_size[0] - 1

            if x >= track_size[1]:
                x = track_size[1] - 1

            track[y, x] = reward

        fig = plt.figure(1, figsize=(12, 16))
        ax = fig.add_subplot(111)

        shifted_center_line = [[point[0] + x_shift, point[1] + y_shift] for point
                               in center_line]
        shifted_inner_border = [[point[0] + x_shift, point[1] + y_shift] for point
                                in inner_border]
        shifted_outer_border = [[point[0] + x_shift, point[1] + y_shift] for point
                                in outer_border]

        PlottingUtils.print_border(ax, shifted_center_line, shifted_inner_border,
                                   shifted_outer_border)

        return track


class EvaluationUtils:
    @staticmethod
    def analyse_single_evaluation(log_file, inner_border, outer_border, episodes=5,
                                  min_progress=None):
        eval_df = SimulationLogsIO.load_to_pandas(log_file)

        for e in range(episodes):
            episode_df = eval_df[eval_df['episode'] == e]
            PlottingUtils.plot_grid_world(episode_df, inner_border,
                                          outer_border, min_progress=min_progress)

    @staticmethod
    def analyse_multiple_race_evaluations(logs, inner_border, outer_border, min_progress=None):
        for log in logs:
            EvaluationUtils.analyse_single_evaluation(
                log[0], inner_border, outer_border, min_progress=min_progress)

    @staticmethod
    def download_and_analyse_multiple_race_evaluations(
        log_folder,
        l_inner_border,
        l_outer_border,
        not_older_than=None,
        older_than=None,
        log_group='/aws/deepracer/leaderboard/SimulationJobs',
        min_progress=None
    ):
        logs = cw.download_all_logs("%s/deepracer-eval-" % log_folder,
                                    log_group, not_older_than, older_than)

        EvaluationUtils.analyse_multiple_race_evaluations(
            logs, l_inner_border, l_outer_border, min_progress=min_progress)


class NewRewardUtils:
    @staticmethod
    def df_to_params(df_row, waypoints):
        from ..tracks.track_utils import GeometryUtils as gu
        waypoint = df_row['closest_waypoint']
        before = waypoint - 1
        if waypoints[waypoint].tolist() == waypoints[before].tolist():
            before -= 1
        after = (waypoint + 1) % len(waypoints)

        if waypoints[waypoint].tolist() == waypoints[after].tolist():
            after = (after + 1) % len(waypoints)

        current_location = np.array([df_row['x'], df_row['y']])

        closest_point = gu.get_a_point_on_a_line_closest_to_point(
            waypoints[before],
            waypoints[waypoint],
            [df_row['x'], df_row['y']]
        )

        if gu.is_point_roughly_on_the_line(
            waypoints[before],
            waypoints[waypoint],
            closest_point[0], closest_point[1]
        ):
            closest_waypoints = [before, waypoint]
        else:
            closest_waypoints = [waypoint, after]

            closest_point = gu.get_a_point_on_a_line_closest_to_point(
                waypoints[waypoint],
                waypoints[after],
                [df_row['x'], df_row['y']]
            )

        params = {
            'x': df_row['x'] / 100,
            'y': df_row['y'] / 100,
            'speed': df_row['throttle'],
            'steps': df_row['steps'],
            'progress': df_row['progress'],
            'heading': df_row['yaw'] * 180 / 3.14,
            'closest_waypoints': closest_waypoints,
            'steering_angle': df_row['steer'] * 180 / 3.14,
            'waypoints': waypoints / 100,
            'distance_from_center':
                gu.get_vector_length(
                    (
                        closest_point -
                        current_location
                    ) / 100),
            'timestamp': df_row['timestamp'],
            # TODO I didn't need them yet. DOIT
            'track_width': 0.60,
            'is_left_of_center': None,
            'all_wheels_on_track': True,
            'is_reversed': False,
        }

        return params

    @staticmethod
    def new_reward(panda, center_line, reward_module, verbose=False):
        import importlib
        importlib.invalidate_caches()
        rf = importlib.import_module(reward_module)
        importlib.reload(rf)

        reward = rf.Reward(verbose=verbose)

        new_rewards = []
        for index, row in panda.iterrows():
            new_rewards.append(
                reward.reward_function(NewRewardUtils.df_to_params(row, center_line)))

        panda['new_reward'] = new_rewards


class ActionBreakdownUtils:
    @staticmethod
    def determine_action_names(df):
        keys = sorted(df.groupby(["action", "steer", "throttle"]).keys(), lambda x: x[0])

        return ["A:%s S:%s%s, T:%s" % (
            key[0],
            abs(key[1]),
            " left" if keys[1] > 0 else " right" if keys[1] < 0 else "",
            key[2]
        ) for key in keys]

    @staticmethod
    def make_error_boxes(ax, xdata, ydata, xerror, yerror, facecolor='r',
                         edgecolor='r', alpha=0.3):
        # Create list for all the error patches
        errorboxes = []

        # Loop over data points; create box from errors at each point
        for x, y, xe, ye in zip(xdata, ydata, xerror.T, yerror.T):
            rect = Rectangle((x - xe[0], y - ye[0]), xe.sum(), ye.sum())
            errorboxes.append(rect)

        # Create patch collection with specified colour/alpha
        pc = PatchCollection(errorboxes, facecolor=facecolor, alpha=alpha,
                             edgecolor=edgecolor)

        # Add collection to axes
        ax.add_collection(pc)

        return 0

    @staticmethod
    def action_breakdown(df, iteration_ids, track_breakdown, center_line,
                         inner_border, outer_border,
                         action_names=None):

        if not action_names:
            action_names = ActionBreakdownUtils.determine_action_names(df)

        fig = plt.figure(figsize=(16, 32))

        if type(iteration_ids) is not list:
            iteration_ids = [iteration_ids]

        wpts_array = center_line

        for iter_num in iteration_ids:
            # Slice the data frame to get all episodes in that iteration
            df_iter = df[(iter_num == df['iteration'])]
            n_steps_in_iter = len(df_iter)
            print('Number of steps in iteration=', n_steps_in_iter)

            th = 0.8
            for idx in range(len(action_names)):
                ax = fig.add_subplot(6, 2, 2 * idx + 1)
                PlottingUtils.print_border(ax, center_line, inner_border, outer_border)

                df_slice = df_iter[df_iter['reward'] >= th]
                df_slice = df_slice[df_slice['action'] == idx]

                ax.plot(df_slice['x'], df_slice['y'], 'b.')

                for idWp in track_breakdown.vert_lines:
                    ax.text(wpts_array[idWp][0],
                            wpts_array[idWp][1] + 20,
                            str(idWp),
                            bbox=dict(facecolor='red', alpha=0.5))

                # ax.set_title(str(log_name_id) + '-' + str(iter_num) + ' w rew >= '+str(th))
                ax.set_ylabel(action_names[idx])

                # calculate action way point distribution
                action_waypoint_distribution = list()
                for idWp in range(len(wpts_array)):
                    action_waypoint_distribution.append(
                        len(df_slice[df_slice['closest_waypoint'] == idWp]))

                ax = fig.add_subplot(6, 2, 2 * idx + 2)

                # Call function to create error boxes
                _ = ActionBreakdownUtils.make_error_boxes(ax,
                                                          track_breakdown.segment_x,
                                                          track_breakdown.segment_y,
                                                          track_breakdown.segment_xerr,
                                                          track_breakdown.segment_yerr)

                for tt in range(len(track_breakdown.track_segments)):
                    ax.text(track_breakdown.track_segments[tt][0],
                            track_breakdown.track_segments[tt][1],
                            track_breakdown.track_segments[tt][2])

                ax.bar(np.arange(len(wpts_array)), action_waypoint_distribution)
                ax.set_xlabel('waypoint')
                ax.set_ylabel('# of actions')
                ax.legend([action_names[idx]])
                ax.set_ylim((0, 150))

        plt.show()
        plt.clf()
