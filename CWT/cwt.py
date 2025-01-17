"""Module that implements the CWT using a trainable complex morlet wavelet. By Nicolas I. Tapia"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import numpy as np
import tensorflow as tf
from tensorflow.keras.layers import Layer

class ContinuousWaveletTransform(Layer):
    """CWT layer implementation in Tensorflow for GPU acceleration."""
    def __init__(self, n_scales, border_crop=0, stride=1, name='CWT',
                 outputformat='Complex',  data_format='channels_last' ):
        """
        Args:
            n_scales: (int) Number of scales for the scalogram.
            border_crop: (int) Non-negative integer that specifies the number
                of samples to be removed at each border after computing the cwt.
                This parameter allows to input a longer signal than the final
                desired size to remove border effects of the CWT. Default 0.
            stride: (int) The stride of the sliding window across the input.
                Default is 1.
        """
        super(ContinuousWaveletTransform, self).__init__(name=name)
        self.n_scales = n_scales
        self.border_crop = border_crop
        self.stride = stride
        self.outputformat = outputformat
        self.data_format = data_format
        self.real_part, self.imaginary_part = self._build_wavelet_bank()

    def _build_wavelet_bank(self):
        """Needs implementation to compute the real and imaginary parts
        of the wavelet bank. Each part is expected to have shape
        [1, kernel_size, 1, n_scales]."""
        real_part = None
        imaginary_part = None
        return real_part, imaginary_part

    @tf.function
    def call(self, inputs):
        """
        Computes the CWT with the specified wavelet bank.
        If the signal has more than one channel, the CWT is computed for
        each channel independently and stacked at the end along the
        channel axis.

        Args:
            inputs: (tensor) A batch of 1D tensors of shape
                [batch_size, time_len, n_channels].
        Returns:
            Scalogram tensor with real and imaginary parts for each input
            channels. The shape of this tensor is
            [batch_size, time_len, n_scales, 2 * n_channels]
        """

        # Generate the scalogram
        border_crop = self.border_crop // self.stride #int(self.border_crop / self.stride)
        start = border_crop
        end = (-border_crop) if (border_crop > 0) else None

        if self.data_format == 'channels_last' :
            inputs = tf.transpose(a=inputs, perm=[0, 2, 1]) # [batch, time_len, n_channels] -> [batch, n_channels,  time_len]

        # [batch, n_channels,  time_len] -> [batch, n_channel, 1, time_len, 1]
        inputs_expand = tf.expand_dims(inputs, axis=2)
        inputs_expand = tf.expand_dims(inputs_expand, axis=4)

        out_real = tf.nn.conv2d(
            input=inputs_expand, filters=self.real_part,
            strides=[1, 1, self.stride, 1], padding="SAME")
        out_imag = tf.nn.conv2d(
            input=inputs_expand, filters=-self.imaginary_part,
            strides=[1, 1, self.stride, 1], padding="SAME")

        #Crop and drop redundant axis to create [batch, n_channels, time, n_scales)]
        out_real = out_real[:, :, 0, start:end, :]
        out_imag = out_imag[:, :, 0, start:end, :]

        if self.outputformat == 'magnitude':
            scalograms = tf.sqrt(out_real**2 + out_imag**2)  # magnitude [batch, n_channels, time, n_scales)]
        elif self.outputformat == 'phase':
            scalograms = tf.math.atan2(out_imag, out_real)  # phase [batch, n_channels, time, n_scales)]
        else:
            scalograms = tf.concat([out_real, out_imag], axis=1) # complex [batch, 2*n_channels, time, n_scales)]

        if self.data_format == 'channels_last' :
            scalograms = tf.transpose(a=scalograms, perm=[0, 2, 3, 1]) #[batch, time, n_scales, channels]

        return scalograms


class ComplexMorletCWT(ContinuousWaveletTransform):
    """CWT with the complex Morlet wavelet filter bank."""
    def __init__(
            self,
            wavelet_width,
            fs,
            lower_freq,
            upper_freq,
            n_scales,
            size_factor=1.0,
            trainable=False,
            border_crop=0,
            stride=1,
            name='ComplexMorletCWT',
            output='complex',
            data_format='channels_last'):
        """
        Computes the complex morlet wavelets

        The mother wavelet is defined as:

        PSI(t) = (1 / Z) * exp(j * 2 * pi * t) * exp(-(t^2) / beta)

        Where:
        beta: wavelet width
        t: k / fs the time axis
        Z: A normalization constant that depends on beta. We want to
        have unit gain at each scale, so we use:
        Z: fs * sqrt(pi * beta) / 2
        And the scaled wavelets are computed as:
        PSI_s(t) = PSI(t / scale) / scale

        Greater wavelet widths lead to more duration of the wavelet in time,
        leading to better frequency resolution but worse time resolution.
        Scales will be automatically computed from the given frequency range and
        the number of desired scales. The scales increase exponentially
        as commonly recommended.

        A gaussian window is commonly truncated at 3 standard deviations from
        the mean. Therefore, we truncate the wavelets at the interval
        |t| <= size_factor * scale * sqrt(4.5 * wavelet_width)
        where size_factor >= 1 can be optionally set to relax this truncation.
        This might be useful when allowing the wavelet width to be trainable.
        Given this heuristic, the wavelet width can be thought in terms of the
        number of effective cycles that the wavelet completes.
        If you want N effective cycles, then you should set
        beta = N^2 / 18
        For example, 4 effective cycles are observed when beta approx 0.9.

        Args:
            wavelet_width: (float o tensor) wavelet width.
            fs: (float) Sampling frequency of the application.
            lower_freq: (float) Lower frequency of the scalogram.
            upper_freq: (float) Upper frequency of the scalogram.
            n_scales: (int) Number of scales for the scalogram.
            size_factor: (float) Factor by which the size of the kernels will
                be increased with respect to the original size. Default 1.0.
            trainable: (boolean) If True, the wavelet width is trainable.
                Default to False.
            border_crop: (int) Non-negative integer that specifies the number
                of samples to be removed at each border after computing the cwt.
                This parameter allows to input a longer signal than the final
                desired size to remove border effects of the CWT. Default 0.
            stride: (int) The stride of the sliding window across the input.
                Default is 1.
        """

        # Checking
        if lower_freq > upper_freq:
            raise ValueError("lower_freq should be lower than upper_freq")
        if lower_freq < 0:
            raise ValueError("Expected positive lower_freq.")
        if output not in ['complex', 'magnitude', 'phase']:
            raise ValueError("Expected output to be 'complex', 'magnitude' or 'phase'.")
        if data_format not in ['channels_last', 'channels_first']:
            raise ValueError("Expected output to be 'channels_last' or 'channels_first'.")



        self.initial_wavelet_width = wavelet_width
        self.fs = fs
        self.lower_freq = lower_freq
        self.upper_freq = upper_freq
        self.size_factor = size_factor
        self.trainable = trainable
        # Generate initial and last scale
        s_0 = 1 / self.upper_freq
        s_n = 1 / self.lower_freq
        # Generate the array of scales
        base = np.power(s_n / s_0, 1 / (n_scales - 1))
        self.scales = s_0 * np.power(base, np.arange(n_scales))
        # Generate the frequency range
        self.frequencies = 1 / self.scales
        # Trainable wavelet width value
        self.wavelet_width = tf.Variable(
            initial_value=self.initial_wavelet_width,
            trainable=self.trainable,
            name='wavelet_width',
            dtype=tf.float32)
        super().__init__(n_scales, border_crop, stride, name, output, data_format)

    def _build_wavelet_bank(self):
        # Generate the wavelets
        # We will make a bigger wavelet in case the width grows
        # For the size of the wavelet we use the initial width value.
        # |t| < truncation_size => |k| < truncation_size * fs
        truncation_size = self.scales.max() * np.sqrt(4.5 * self.initial_wavelet_width) * self.fs
        one_side = int(self.size_factor * truncation_size)
        kernel_size = 2 * one_side + 1
        k_array = np.arange(kernel_size, dtype=np.float32) - one_side
        t_array = k_array / self.fs  # Time units
        # Wavelet bank shape: 1, kernel_size, 1, n_scales
        wavelet_bank_real = []
        wavelet_bank_imag = []
        for scale in self.scales:
            norm_constant = tf.sqrt(np.pi * self.wavelet_width) * scale * self.fs / 2.0
            scaled_t = t_array / scale
            exp_term = tf.exp(-(scaled_t ** 2) / self.wavelet_width)
            kernel_base = exp_term / norm_constant
            kernel_real = kernel_base * np.cos(2 * np.pi * scaled_t)
            kernel_imag = kernel_base * np.sin(2 * np.pi * scaled_t)
            wavelet_bank_real.append(kernel_real)
            wavelet_bank_imag.append(kernel_imag)
        # Stack wavelets (shape = kernel_size, n_scales)
        wavelet_bank_real = tf.stack(wavelet_bank_real, axis=-1)
        wavelet_bank_imag = tf.stack(wavelet_bank_imag, axis=-1)
        # Give it proper shape for convolutions
        # -> shape: 1, kernel_size, n_scales
        wavelet_bank_real = tf.expand_dims(wavelet_bank_real, axis=0)
        wavelet_bank_imag = tf.expand_dims(wavelet_bank_imag, axis=0)
        # -> shape: 1, kernel_size, 1, n_scales
        wavelet_bank_real = tf.expand_dims(wavelet_bank_real, axis=2)
        wavelet_bank_imag = tf.expand_dims(wavelet_bank_imag, axis=2)
        return wavelet_bank_real, wavelet_bank_imag
