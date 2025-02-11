import numpy as np
from skimage import exposure
import matplotlib.pyplot as plt
import logging
import random
from scipy.linalg import eigh
from numpy import linalg as LA
import os
import glob
import shutil
import pathlib
from matplotlib.colors import ListedColormap, BoundaryNorm
import matplotlib.patches as mpatches
from sklearn.metrics import confusion_matrix, accuracy_score
import seaborn as sns

import torch
import torch.nn as nn
import torch.utils.data
from torchvision import transforms

from torchcvnn.datasets import ALOSDataset, PolSFDataset, Bretigny


class LogAmplitudeTransform:
    def __init__(self, characteristics):
        # Store the channel characteristics
        self.characteristics = characteristics

    def __call__(self, element):

        if isinstance(element, np.ndarray):
            tensor = torch.as_tensor(
                np.stack(
                    (
                        element[0],
                        element[1],
                        element[2],
                    ),
                    axis=-1,
                ).transpose(2, 0, 1),
                dtype=torch.complex64,
            )
        elif isinstance(element, dict):
            if len(element) == 3:
                tensor = torch.as_tensor(
                    np.stack(
                        (
                            element["HH"],
                            element["HV"],
                            element["VV"],
                        ),
                        axis=-1,
                    ).transpose(2, 0, 1),
                    dtype=torch.complex64,
                )
            elif len(element) == 4:
                tensor = torch.as_tensor(
                    np.stack(
                        (
                            element["HH"],
                            (element["HV"] + element["VH"]) / 2,
                            element["VV"],
                        ),
                        axis=-1,
                    ).transpose(2, 0, 1),
                    dtype=torch.complex64,
                )

        new_tensor = tensor
        m = 2e-2
        M = 40

        amplitude = torch.clip(torch.abs(tensor), m, M)
        phase = torch.angle(tensor)

        transformed_amplitude = (torch.log10(amplitude) - np.log10(m)) / (
            np.log10(M) - np.log10(m)
        )
        # Recombine to form new complex tensor
        new_tensor = transformed_amplitude * torch.exp(1j * phase)

        return new_tensor


def pauli_transform(SAR_img):
    S_HH = SAR_img[0, :, :]
    S_HV = SAR_img[1, :, :]
    S_VV = SAR_img[2, :, :]
    return (1 / np.sqrt(2)) * np.stack(
        (
            S_HH - S_VV,
            2 * S_HV,
            S_HH + S_VV,
        ),
        dtype=np.complex64,
    )


def cameron_transform(SAR_img):
    S_HH = SAR_img[0, :, :]
    S_HV = SAR_img[1, :, :]
    S_VH = S_HV
    S_VV = SAR_img[2, :, :]

    # Calculate the norm of the backscatter vectors
    a = np.sqrt(
        S_HH * np.conj(S_HH)
        + S_HV * np.conj(S_HV)
        + S_VH * np.conj(S_VH)
        + S_VV * np.conj(S_VV)
    )

    # Determine the Pauli parameters
    Alpha = 1 / np.sqrt(2) * (S_HH + S_VV)
    Beta = 1 / np.sqrt(2) * (S_HH - S_VV)
    Gamma = 1 / np.sqrt(2) * (S_HV + S_VH)
    Delta = 1 / np.sqrt(2) * (S_VH - S_HV)

    # Determine the parameter x
    sin_x = (Beta * np.conj(Gamma) + np.conj(Beta) * Gamma) / np.sqrt(
        (Beta * np.conj(Gamma) + np.conj(Beta) * Gamma) ** 2
        + (np.abs(Beta) ** 2 - np.abs(Gamma) ** 2) ** 2
    )
    cos_x = (np.abs(Beta) ** 2 - np.abs(Gamma) ** 2) / np.sqrt(
        (Beta * np.conj(Gamma) + np.conj(Beta) * Gamma) ** 2
        + (np.abs(Beta) ** 2 - np.abs(Gamma) ** 2) ** 2
    )

    x = (
        np.arccos(cos_x) * (sin_x >= 0)
        + np.arcsin(sin_x) * ((sin_x < 0) & (cos_x >= 0))
        + (-np.arcsin(sin_x) - np.pi) * ((sin_x < 0) & (cos_x < 0))
    ) * (
        (np.abs(Beta) ** 2 - np.abs(Gamma) ** 2 != 0)
        | (Beta * np.conj(Gamma) + np.conj(Beta) * Gamma != 0)
    )

    # Determine DS
    Scalar = (
        1
        / np.sqrt(2)
        * (
            S_HH * np.cos(x / 2)
            + S_HV * np.sin(x / 2)
            + S_VH * np.sin(x / 2)
            - S_VV * np.cos(x / 2)
        )
    )
    DS_1 = 1 / np.sqrt(2) * (Alpha + np.cos(x / 2) * Scalar)
    DS_2 = 1 / np.sqrt(2) * np.sin(x / 2) * Scalar
    DS_3 = 1 / np.sqrt(2) * np.sin(x / 2) * Scalar
    DS_4 = 1 / np.sqrt(2) * (Alpha - (np.cos(x / 2) * Scalar))

    # Determine S_max
    S_max = np.sqrt(
        DS_1 * np.conj(DS_1)
        + DS_2 * np.conj(DS_2)
        + DS_3 * np.conj(DS_3)
        + DS_4 * np.conj(DS_4)
    )
    S_max1 = DS_1 / S_max
    S_max2 = DS_2 / S_max
    S_max3 = DS_3 / S_max
    S_max4 = DS_4 / S_max

    # Determine S_rec
    S_rec1 = S_HH
    S_rec2 = 1 / 2 * (S_HV + S_VH)
    S_rec3 = 1 / 2 * (S_HV + S_VH)
    S_rec4 = S_VV

    # Calculate DS_rec
    Scalar_rec = (
        1
        / np.sqrt(2)
        * (
            S_rec1 * np.cos(x / 2)
            + S_rec2 * np.sin(x / 2)
            + S_rec3 * np.sin(x / 2)
            - S_rec4 * np.cos(x / 2)
        )
    )
    DS_rec1 = 1 / np.sqrt(2) * (Alpha + np.cos(x / 2) * Scalar_rec)
    DS_rec2 = 1 / np.sqrt(2) * np.sin(x / 2) * Scalar_rec
    DS_rec3 = 1 / np.sqrt(2) * np.sin(x / 2) * Scalar_rec
    DS_rec4 = 1 / np.sqrt(2) * (Alpha - (np.cos(x / 2) * Scalar_rec))

    # Determine S_min
    S_min1 = S_rec1 - DS_rec1
    S_min2 = S_rec2 - DS_rec2
    S_min3 = S_rec3 - DS_rec3
    S_min4 = S_rec4 - DS_rec4

    S_max = np.sqrt(
        S_min1 * np.conj(S_min1)
        + S_min2 * np.conj(S_min2)
        + S_min3 * np.conj(S_min3)
        + S_min4 * np.conj(S_min4)
    )

    S_min1 /= S_max
    S_min2 /= S_max
    S_min3 /= S_max
    S_min4 /= S_max

    # Determine S_nr
    S_nr = Delta / np.abs(Delta)  # impossible à déterminer

    # Determine Theta_rec
    Theta_rec = np.arccos(
        np.sqrt(
            S_rec1 * np.conj(S_rec1)
            + S_rec2 * np.conj(S_rec2)
            + S_rec3 * np.conj(S_rec3)
            + S_rec4 * np.conj(S_rec4)
        )
        / a
    )

    # Determine Tau
    Scalar_tau = (
        S_rec1 * np.conj(DS_1)
        + S_rec2 * np.conj(DS_2)
        + S_rec3 * np.conj(DS_3)
        + S_rec4 * np.conj(DS_4)
    )
    S_max_tau = np.sqrt(
        S_rec1 * np.conj(S_rec1)
        + S_rec2 * np.conj(S_rec2)
        + S_rec3 * np.conj(S_rec3)
        + S_rec4 * np.conj(S_rec4)
    )
    S_maxx_tau = np.sqrt(
        DS_1 * np.conj(DS_1)
        + DS_2 * np.conj(DS_2)
        + DS_3 * np.conj(DS_3)
        + DS_4 * np.conj(DS_4)
    )

    Tau = np.arccos(np.abs(Scalar_tau / (S_max_tau * S_maxx_tau)))

    # Determination of Psi_0
    Psi_1 = -1 / 4 * x

    Psi_11 = Psi_1
    Psi_12 = Psi_1 + np.pi / 2
    Psi_13 = Psi_1 - np.pi / 2

    def compute_A_components(Psi):
        A_1 = (
            (np.cos(Psi) ** 2) * DS_rec1
            - (np.cos(Psi) * np.sin(Psi)) * DS_rec2
            - (np.cos(Psi) * np.sin(Psi)) * DS_rec3
            + (np.sin(Psi) ** 2) * DS_rec4
        )
        A_4 = (
            (np.sin(Psi) ** 2) * DS_rec1
            + (np.cos(Psi) * np.sin(Psi)) * DS_rec2
            + (np.cos(Psi) * np.sin(Psi)) * DS_rec3
            + (np.cos(Psi) ** 2) * DS_rec4
        )
        return np.abs(A_1), np.abs(A_4)

    A1_1, A1_4 = compute_A_components(Psi_11)
    A2_1, A2_4 = compute_A_components(Psi_12)
    A3_1, A3_4 = compute_A_components(Psi_13)

    Psi_0 = Psi_11 * ((Psi_11 > -np.pi / 2) & (Psi_11 <= np.pi / 2) & (A1_1 >= A1_4))
    Psi_0 = Psi_0 + Psi_12 * (
        (Psi_12 > -np.pi / 2) & (Psi_12 <= np.pi / 2) & (A2_1 >= A2_4) & (Psi_0 == 0)
    )
    Psi_0 = Psi_0 + Psi_13 * (
        (Psi_13 > -np.pi / 2) & (Psi_13 <= np.pi / 2) & (A3_1 >= A3_4) & (Psi_0 == 0)
    )

    # Determination of Psi_D
    A1_1, A1_4 = compute_A_components(Psi_0)

    I_a = A1_1 == A1_4
    I_b = A1_1 == -A1_4

    Psi_D = (Psi_0 - np.pi / 2) * ((Psi_0 > np.pi / 4) & (I_a | I_b))
    Psi_D = Psi_D + Psi_0 * (
        ((Psi_0 > -np.pi / 4) & (Psi_0 <= np.pi / 4) & (Psi_D == 0)) & (I_a | I_b)
    )
    Psi_D = Psi_D + (Psi_0 + np.pi / 2) * (
        ((Psi_0 <= -np.pi / 4) & (Psi_D == 0)) & (I_a | I_b)
    )
    Psi_D = Psi_D + Psi_0 * ((I_a == 0) & (I_b == 0))

    return (
        S_max1,
        S_max2,
        S_max3,
        S_max4,
        S_min1,
        S_min2,
        S_min3,
        S_min4,
        S_nr,
        a,
        Tau,
        Theta_rec,
        Psi_D,
    )


def cameron_classification(
    S_max1,
    S_max2,
    S_max3,
    S_max4,
    S_min1,
    S_min2,
    S_min3,
    S_min4,
    S_nr,
    a,
    Tau,
    Theta_rec,
    Psi_D,
):

    A1 = (
        (np.cos(Psi_D) ** 2) * S_max1
        - (np.cos(Psi_D) * np.sin(Psi_D)) * (S_max2 + S_max3)
        + (np.sin(Psi_D) ** 2) * S_max4
    )
    A4 = (
        (np.sin(Psi_D) ** 2) * S_max1
        + (np.cos(Psi_D) * np.sin(Psi_D)) * (S_max2 + S_max3)
        + (np.cos(Psi_D) ** 2) * S_max4
    )
    z = A4 / A1

    classe = np.zeros(Theta_rec.shape)

    for i in range(Theta_rec.shape[0]):
        for j in range(Theta_rec.shape[1]):
            if Theta_rec[i, j] > np.pi / 4:
                classe[i, j] = 1
            elif Theta_rec[i, j] <= np.pi / 4 and Tau[i, j] > np.pi / 8:
                S1 = (
                    a[i, j]
                    * np.cos(Theta_rec[i, j])
                    * (
                        np.cos(Tau[i, j]) * S_max1[i, j]
                        + np.sin(Tau[i, j]) * S_min1[i, j]
                    )
                )
                S2 = a[i, j] * np.cos(Theta_rec[i, j]) * (
                    np.cos(Tau[i, j]) * S_max2[i, j] + np.sin(Tau[i, j]) * S_min2[i, j]
                ) - a[i, j] * np.sin(Theta_rec[i, j]) * S_nr[i, j] / np.sqrt(2)
                S3 = a[i, j] * np.cos(Theta_rec[i, j]) * (
                    np.cos(Tau[i, j]) * S_max3[i, j] + np.sin(Tau[i, j]) * S_min3[i, j]
                ) + a[i, j] * np.sin(Theta_rec[i, j]) * S_nr[i, j] / np.sqrt(2)
                S4 = (
                    a[i, j]
                    * np.cos(Theta_rec[i, j])
                    * (
                        np.cos(Tau[i, j]) * S_max4[i, j]
                        + np.sin(Tau[i, j]) * S_min4[i, j]
                    )
                )

                Scalarleft = 0.5 * (S1 - S4 - 1j * (S2 + S3))
                Scalarright = 0.5 * (S1 - S4 + 1j * (S2 + S3))

                theta_Tleft = np.arccos(abs(Scalarleft / a[i, j]))
                theta_Tright = np.arccos(abs(Scalarright / a[i, j]))

                if theta_Tleft > np.pi / 4 and theta_Tright > np.pi / 4:
                    classe[i, j] = 2
                else:
                    classifieur = int(theta_Tleft >= theta_Tright)
                    classe[i, j] = classifieur * 3 + (1 - classifieur) * 4

            elif Theta_rec[i, j] <= np.pi / 4 and Tau[i, j] <= np.pi / 8:
                z_conj = np.conj(z[i, j])
                D_Trihedre = np.arccos(
                    max(abs(1 + z_conj), abs(1 + z_conj))
                    / np.sqrt(2 * (1 + abs(z[i, j]) ** 2))
                )
                D_Dihedre = np.arccos(
                    max(abs(1 - z_conj), abs(-1 + z_conj))
                    / np.sqrt(2 * (1 + abs(z[i, j]) ** 2))
                )
                D_Dipole = np.arccos(
                    max(1, abs(z_conj)) / np.sqrt((1 + abs(z[i, j]) ** 2))
                )
                D_Cylindre = np.arccos(
                    max(abs(1 + z_conj / 2), abs(1 / 2 + z_conj))
                    / np.sqrt(5 / 4 * (1 + abs(z[i, j]) ** 2))
                )
                D_Dihedreetroit = np.arccos(
                    max(abs(1 - z_conj / 2), abs(-1 / 2 + z_conj))
                    / np.sqrt(5 / 4 * (1 + abs(z[i, j]) ** 2))
                )
                D_QuartOnde = np.arccos(
                    max(abs(1 + 1j * z_conj), abs(1j + z_conj))
                    / np.sqrt(2 * (1 + abs(z[i, j]) ** 2))
                )

                D = np.array(
                    [
                        D_Trihedre,
                        D_Dihedre,
                        D_Dipole,
                        D_Cylindre,
                        D_Dihedreetroit,
                        D_QuartOnde,
                    ]
                )

                classifieur = np.min(D)

                if classifieur > np.pi / 4:
                    classe[i, j] = 5
                else:
                    classe[i, j] = np.argmin(D) + 6

    return classe


def krogager_transform(SAR_img):
    S_HH = SAR_img[0, :, :]
    S_HV = SAR_img[1, :, :]
    S_VV = SAR_img[2, :, :]

    S_RR = 1j * S_HV + 0.5 * (S_HH - S_VV)
    S_LL = 1j * S_HV - 0.5 * (S_HH - S_VV)
    S_RL = 1j / 2 * (S_HH + S_VV)

    return np.stack(
        (
            np.minimum(np.abs(S_RR), np.abs(S_LL)),
            np.abs(np.abs(S_RR) - np.abs(S_LL)),
            np.abs(S_RL),
        ),
    )


def exp_amplitude_transform(tensor):
    tensor = torch.from_numpy(tensor)
    m = 2e-2
    M = 40

    amplitude = torch.abs(tensor)
    phase = torch.angle(tensor)

    inv_transformed_amplitude = torch.clip(
        torch.exp(((np.log10(M) - np.log10(m)) * amplitude + np.log10(m)) * np.log(10)),
        0,
        10**9,
    )

    # Recombine to form new complex tensor
    new_tensor = inv_transformed_amplitude * torch.exp(1j * phase)

    return new_tensor


def equalize(image, p2=None, p98=None):
    """
    Automatically adjust contrast of the SAR image
    Input: intensity or amplitude in dB scale
    """
    img = np.log10(np.abs(image))
    if not p2:
        p2, p98 = np.percentile(img, (2, 98))
    img_resc = np.round(
        exposure.rescale_intensity(img, in_range=(p2, p98), out_range=(0, 1)) * 255
    ).astype(np.uint8)

    return img_resc, (p2, p98)


def angular_distance(image1, image2):
    """
    Compute the angular distance between two phase angles, phase1 and phase2, with results in [-pi, pi].
    """
    diff = np.angle(image1) - np.angle(image2) + np.pi
    angular_dist = np.mod(diff, 2 * np.pi) - np.pi
    return angular_dist


def plot_phase(image):
    """
    Plot the phase of a PolSAR image and normalize it to [0, 255].
    """
    phase_image = np.angle(image)  # Phase in [-π, π)
    # Normalize phase to [0, 1]
    normalized_phase = (phase_image + np.pi) / (2 * np.pi)
    # Scale to [0, 255] and convert to integer
    scaled_phase = np.round(normalized_phase * 255).astype(np.uint8)
    return scaled_phase


def plot_angular_distance(image1, image2):
    """
    Plot the phase of a PolSAR image and normalize it to [0, 255].
    """
    ang_distance_image = angular_distance(image1, image2)
    # Normalize phase to [0, 1]
    normalized_ang_distance_image = (ang_distance_image + np.pi) / (2 * np.pi)
    # Scale to [0, 255] and convert to integer
    scaled_ang_distance_image = np.round(normalized_ang_distance_image * 255).astype(
        np.uint8
    )
    return scaled_ang_distance_image


def plot_fourier_transform_amplitude_phase(image):
    amplitude_ft_images = []
    phase_ft_vectors = []

    for channel in range(image.shape[0]):
        fft_img = np.fft.fftshift(np.fft.fft2(image[channel, :, :]))
        amplitude = np.abs(fft_img)
        phase = np.angle(fft_img)

        # Compute magnitude spectrum and use log scale for visibility
        amplitude_ft_magnitude = np.log(np.abs(amplitude) + 1)
        phase_vectors = (np.cos(phase), np.sin(phase))

        amplitude_ft_images.append(amplitude_ft_magnitude)
        phase_ft_vectors.append(phase_vectors)

    return amplitude_ft_images, phase_ft_vectors


########################################################################################################################
### CALCULATE THE MEANS OF THE CLASSES AFTER H-alpha INITIALIZATION
########################################################################################################################

### Calculate the means of the classes. This function will also be used below later, after a continuous update of classes.
### As input it takes the image of stacked up covariances, and a mask of classes.


def calculate_means_of_classes(image_of_stacked_covariances, classes_H_alpha):

    list_of_classes = [1, 2, 4, 5, 6, 7, 8, 9]  ### class 3 is not possible
    dictionary_of_means = {}

    for k in list_of_classes:
        ### Create a mask for the image which is TRUE where a pixel belongs to the class, and FALSE otherwise.
        mask = classes_H_alpha == k
        size_of_mask_1, size_of_mask_2 = mask.shape
        mask = np.reshape(mask, (size_of_mask_1, size_of_mask_2, 1))
        ### Multiply image with mask, i.e. all pixels/covariances not belonging to the initial class k will be set to zero.
        cov_times_mask = image_of_stacked_covariances * mask
        cov_times_mask = np.reshape(
            cov_times_mask,
            (
                cov_times_mask.shape[0] * cov_times_mask.shape[1],
                cov_times_mask.shape[2],
            ),
        )
        ### Since all other entries are set to zero, taking the mean over the whole image equals the mean of just class k.
        mean_of_class = np.mean(cov_times_mask, axis=0)
        dictionary_of_means["mean" + str(k)] = mean_of_class

    return dictionary_of_means


def h_alpha(pauli_radar_image):
    ########################################################################################################################
    fullsamples = pauli_radar_image
    s1, s2, p = fullsamples.shape
    son = 7
    ########################################################################################################################

    ### Create variables that will be used for the H-alpha decomposition.
    p_vector = np.zeros(3)
    alpha_vector = np.zeros(3)
    H_alpha = np.zeros((s1 - (son - 1), s2 - (son - 1), 2))

    ### This will contain the original classes after the H alpha initialization.
    classes_H_alpha_original = np.zeros((s1 - (son - 1), s2 - (son - 1)), dtype=int)

    ### This is the image containing as pixels the local covariances 'stacked up'.
    ### Since a part of the edge is lost, it is slightly smaller than the original image.
    covariances_stacked = np.zeros(
        (s1 - (son - 1), s2 - (son - 1), p * p), dtype=complex
    )

    for k in range(s1 - (son - 1)):
        for l in range(s2 - (son - 1)):
            ###### calculate the local empirical covariance matrix (or second moment) of the pixel and neighborhood under consideration
            local_data_matrix = np.reshape(
                fullsamples[k : k + son, l : l + son, :], (son**2, p)
            )
            local_covariance = np.dot(
                np.conjugate(local_data_matrix).T, local_data_matrix
            ) / (son**2)
            ##### stack up the covariance matrices in one large vector
            local_covariance_stacked = np.reshape(local_covariance, (1, 1, p * p))
            covariances_stacked[k, l, :] = local_covariance_stacked
            ##### spectral decomposition of the local covariance - calculate H and alpha values for each pixel
            eigenvalues, eigenvectors = eigh(local_covariance)
            D = np.diag(eigenvalues)
            U = eigenvectors
            # spectral_dec = reduce(np.dot, [U, D, np.conj(U).T])
            ##### Calculate the H alpha decomposition - i.e. the initialization of the classes!
            for i in range(3):
                p_vector[i] = eigenvalues[i] / np.sum(eigenvalues)
                alpha_vector[i] = np.arccos(abs(eigenvectors[0, i]))
            H = -np.dot(p_vector, np.log(p_vector))
            if H > 1.0:
                H = 1.0
            if np.isnan(H):
                H = 0
            alpha = np.dot(p_vector, alpha_vector) * (180.0 / np.pi)
            if alpha > 90:
                alpha = 90
            H_alpha[k, l, 0] = H
            H_alpha[k, l, 1] = alpha

            ### The original class assigned to the pixels via the initial H - alpha decomposition is simply determined by a distinction of the different cases.
            if H <= 0.5:
                if alpha <= 42.5:
                    classes_H_alpha_original[k, l] = 9
                elif alpha <= 47.5:
                    classes_H_alpha_original[k, l] = 8
                elif alpha <= 90:
                    classes_H_alpha_original[k, l] = 7
            elif H <= 0.9:
                if alpha <= 40:
                    classes_H_alpha_original[k, l] = 6
                elif alpha <= 50:
                    classes_H_alpha_original[k, l] = 5
                elif alpha <= 90:
                    classes_H_alpha_original[k, l] = 4
            elif H <= 1.0:
                if alpha <= 55:
                    classes_H_alpha_original[k, l] = 2
                elif alpha <= 90:
                    classes_H_alpha_original[k, l] = 1

    ### Create the data matrix by reshaping, which contains all covariances as rows.
    X1 = np.reshape(covariances_stacked, ((s1 - (son - 1)) * (s2 - (son - 1)), p * p))

    return classes_H_alpha_original


def show_images(samples, generated, image_path, last=False):

    num_samples = len(samples)
    num_channels = samples[0].shape[0]

    if last:
        ncols = 12 + 4 * num_channels
    else:
        ncols = 12

    fig, axes = plt.subplots(
        nrows=num_samples,
        ncols=ncols,
        figsize=(5 * ncols, 5 * num_samples),
        constrained_layout=True,
    )
    axes = np.atleast_2d(axes)  # Ensure axes is a 2D array for consistency
    channels = ["HH", "HV", "VV"]
    channels_pauli = ["HH-VV", "2HV", "HH+VV"]
    channels_krogager = ["kd", "kh", "ks"]

    for i in range(num_samples):

        idx = 0
        img_dataset, img_gen = (
            exp_amplitude_transform(samples[i]).numpy(),
            exp_amplitude_transform(generated[i]).numpy(),
        )

        img_dataset_trans = img_dataset.transpose(1, 2, 0)
        img_gen_trans = img_gen.transpose(1, 2, 0)

        pauli_img_dataset = pauli_transform(img_dataset).transpose(1, 2, 0)
        pauli_img_gen = pauli_transform(img_gen).transpose(1, 2, 0)

        krogager_img_dataset = krogager_transform(img_dataset).transpose(1, 2, 0)
        krogager_img_gen = krogager_transform(img_gen).transpose(1, 2, 0)

        """
        print(cameron_transform(img_dataset))
        input()
        cameron_img_dataset = cameron_classification(cameron_transform(img_dataset))
        print(cameron_img_dataset.shape)
        input()
        cameron_img_gen = cameron_classification(cameron_transform(img_gen))
        """
        # Plot amplitude using Pauli decomposition
        eq_dataset, (p2, p98) = equalize(pauli_img_dataset)
        axes[i][idx].imshow(eq_dataset, origin="lower")
        axes[i][idx].set_title(f"Amplitude dataset Pauli basis {i+1}")
        axes[i][idx].axis("off")  # Turn off axes for image plot
        idx += 1

        eq_generated, _ = equalize(pauli_img_gen, p2=p2, p98=p98)
        axes[i][idx].imshow(eq_generated, origin="lower")
        axes[i][idx].set_title(f"Amplitude generated Pauli basis {i+1}")
        axes[i][idx].axis("off")  # Turn off axes for image plot
        idx += 1

        # Plot amplitude using Krogager decomposition
        eq_dataset, (p2, p98) = equalize(krogager_img_dataset)
        axes[i][idx].imshow(eq_dataset, origin="lower")
        axes[i][idx].set_title(f"Amplitude dataset Krogager basis {i+1}")
        axes[i][idx].axis("off")  # Turn off axes for image plot
        idx += 1

        eq_generated, _ = equalize(krogager_img_gen, p2=p2, p98=p98)
        axes[i][idx].imshow(eq_generated, origin="lower")
        axes[i][idx].set_title(f"Amplitude generated Krogager basis {i+1}")
        axes[i][idx].axis("off")  # Turn off axes for image plot
        idx += 1
        """
        # Plot amplitude using Cameron decomposition
        eq_dataset, (p2, p98) = equalize(cameron_img_dataset)
        axes[i][idx].imshow(eq_dataset, origin="lower")
        axes[i][idx].set_title(f"Amplitude dataset Cameron basis {i+1}")
        axes[i][idx].axis("off")  # Turn off axes for image plot
        idx += 1

        eq_generated, _ = equalize(cameron_img_gen, p2=p2, p98=p98)
        axes[i][idx].imshow(eq_generated, origin="lower")
        axes[i][idx].set_title(f"Amplitude generated Cameron basis {i+1}")
        axes[i][idx].axis("off")  # Turn off axes for image plot
        idx += 1
        """

        # Compute pixel-wise amplitude difference and plot histogram in the same figure
        mse_values = (
            np.abs(img_dataset_trans) - np.abs(img_gen_trans)
        ).flatten()  # we don't use the equalize output due to the transform applied to the amplitude

        # Calculate the 5th and 95th quantiles
        q5, q95 = np.percentile(mse_values, [5, 95])

        # Filter the data
        filtered_data = mse_values[(mse_values > q5) & (mse_values < q95)]

        # Plot the histogram of the filtered data

        axes[i][idx].hist(
            filtered_data,
            bins=100,
            alpha=0.75,
        )

        axes[i][idx].set_title(f"Amplitude Difference Histogram {i+1}")
        axes[i][idx].set_xlabel("Amplitude Difference Value")
        axes[i][idx].set_ylabel("Frequency")
        idx += 1

        for ch in range(num_channels):
            axes[i][idx].imshow(
                plot_angular_distance(
                    img_dataset_trans[:, :, ch], img_gen_trans[:, :, ch]
                ),
                cmap="hsv",
                origin="lower",
            )
            axes[i][idx].set_title(f"Angular Distance pixel-wise {i+1} " + channels[ch])
            axes[i][idx].axis("off")  # Turn off axes for image plot
            idx += 1

        # Plot histogram of angular distances for phase images
        axes[i][idx].hist(
            angular_distance(img_dataset_trans, img_gen_trans).flatten(),
            bins=100,
            alpha=0.75,
        )
        axes[i][idx].set_title(f"Angular Distance Histogram {i+1}")
        axes[i][idx].set_xlabel("Angular Distance (radians)")
        axes[i][idx].set_ylabel("Frequency")
        idx += 1

        # Define a custom color map for classes 1 through 9
        class_colors = {
            1: "green",
            2: "yellow",
            4: "blue",
            5: "pink",
            6: "purple",
            7: "red",
            8: "brown",
            9: "gray",
        }

        # Generate a custom color map from the class_colors dictionary
        cmap = ListedColormap([i for i in class_colors.values()])

        # Create bounds and a normalization for the colormap
        bounds = list(class_colors.keys())
        norm = BoundaryNorm(bounds, cmap.N)
        # Create a legend for the classes
        patches = [
            mpatches.Patch(color=class_colors[i], label=f"Class {i}")
            for i in class_colors
        ]

        h_alpha_original = h_alpha(pauli_img_dataset)

        ### Plot the H - alpha initialization, i.e. the mask of classes assigend to the pixels according to the H - alpha decomposition.
        axes[i][idx].imshow(h_alpha_original, origin="lower", cmap=cmap, norm=norm)
        axes[i][idx].legend(handles=patches, bbox_to_anchor=(1.05, 1), loc="upper left")
        axes[i][idx].set_title(f"H_alpha dataset {i+1}")
        axes[i][idx].axis("off")  # Turn off axes for image plot
        idx += 1

        h_alpha_gen = h_alpha(pauli_img_gen)

        axes[i][idx].imshow(h_alpha_gen, origin="lower", cmap=cmap, norm=norm)
        axes[i][idx].legend(handles=patches, bbox_to_anchor=(1.05, 1), loc="upper left")
        axes[i][idx].set_title(f"H_alpha generated {i+1}")
        axes[i][idx].axis("off")  # Turn off axes for image plot
        idx += 1

        print(
            "Accuracy between the H_alpha labels is: "
            + str(
                round(
                    100
                    * accuracy_score(h_alpha_original.flatten(), h_alpha_gen.flatten()),
                    3,
                )
            )
        )
        # confusion matrix
        cm = confusion_matrix(
            h_alpha_original.flatten(), h_alpha_gen.flatten(), normalize="true"
        ).round(decimals=3)
        sns.heatmap(
            cm,
            annot=True,
            fmt=".2g",
            cmap="Blues",
            ax=axes[i][idx],
            xticklabels=list(class_colors.keys()),
            yticklabels=list(class_colors.keys()),
        )
        axes[i][idx].set_xlabel("Reconstructed H_alpha classes")
        axes[i][idx].set_ylabel("Original H_alpha classes")
        axes[i][idx].set_title("Confusion Matrix")
        idx += 1

        # If last, continue with the original functionality for phase and FT amplitude images
        if last:

            # Compute Fourier transforms for amplitude and phase for each channel
            dataset_amplitude_ft, dataset_phase_vectors = (
                plot_fourier_transform_amplitude_phase(img_dataset)
            )
            generated_amplitude_ft, generated_phase_vectors = (
                plot_fourier_transform_amplitude_phase(img_gen)
            )

            for ch in range(num_channels):
                # Plot Fourier Transforms of the amplitude and phase for dataset and generated images
                # base_index = 5 + ch * 4  # Base index for each channel's plots
                axes[i][idx].imshow(dataset_amplitude_ft[ch], cmap="gray")
                axes[i][idx].set_title(f"FT Amp Dataset " + channels[ch])
                axes[i][idx].axis("off")  # Turn off axes for image plot
                idx += 1

                axes[i][idx].imshow(generated_amplitude_ft[ch], cmap="gray")
                axes[i][idx].set_title(f"FT Amp Generated " + channels[ch])
                axes[i][idx].axis("off")  # Turn off axes for image plot
                idx += 1

                X, Y = np.meshgrid(
                    np.arange(samples[0][ch, :, :].shape[1]),
                    np.arange(samples[0][ch, :, :].shape[0]),
                )

                axes[i][idx].quiver(
                    X,
                    Y,
                    dataset_phase_vectors[ch][0],
                    dataset_phase_vectors[ch][1],
                    scale=60,
                )
                axes[i][idx].set_title(f"FT Phase Dataset " + channels[ch])
                axes[i][idx].axis("off")  # Turn off axes for image plot
                idx += 1

                axes[i][idx].quiver(
                    X,
                    Y,
                    generated_phase_vectors[ch][0],
                    generated_phase_vectors[ch][1],
                    scale=60,
                )
                axes[i][idx].set_title(f"FT Phase Generated " + channels[ch])
                axes[i][idx].axis("off")  # Turn off axes for image plot
                idx += 1

    plt.savefig(image_path, bbox_inches="tight", pad_inches=0)
    plt.close()


def get_dataloaders(data_config, use_cuda):
    img_size = (data_config["img_size"], data_config["img_size"])
    img_stride = (data_config["img_stride"], data_config["img_stride"])
    start_row = data_config["crop"]["start_row"]
    start_col = data_config["crop"]["start_col"]
    end_row = data_config["crop"]["end_row"]
    end_col = data_config["crop"]["end_col"]
    valid_ratio = data_config["valid_ratio"]
    batch_size = data_config["batch_size"]
    num_workers = data_config["num_workers"]
    name_dataset = data_config["dataset"]["name"]
    trainpath = data_config["dataset"]["trainpath"]

    logging.info("  - Dataset creation")

    input_transform = LogAmplitudeTransform(data_config["characteristics"])

    if data_config["dataset"]["name"] == "Bretigny":
        train_dataset = eval(
            f"{data_config['dataset']['name']}(root=trainpath, fold='train', transform=input_transform, patch_size=img_size, patch_stride=img_stride)"
        )
        valid_dataset = eval(
            f"{data_config['dataset']['name']}(root=trainpath, fold='valid', transform=input_transform, patch_size=img_size, patch_stride=img_stride)"
        )
        logging.info(f"  - I loaded {len(train_dataset) + len(valid_dataset)} samples")

    else:
        if data_config["dataset"]["name"] == "ALOSDataset":
            trainpath = pathlib.Path(trainpath) / "VOL-ALOS2044980750-150324-HBQR1.1__A"
            base_dataset = eval(
                f"{name_dataset}(volpath=trainpath, transform=input_transform, crop_coordinates=((start_row, start_col), (end_row, end_col)), patch_size=img_size, patch_stride=img_stride)"
            )
        elif data_config["dataset"]["name"] == "PolSFDataset":
            base_dataset = eval(
                f"{data_config['dataset']['name']}(root=trainpath, transform=input_transform, patch_size=img_size, patch_stride=img_stride)"
            )
        logging.info(f"  - I loaded {len(base_dataset)} samples")

        indices = list(range(len(base_dataset)))
        random.shuffle(indices)
        num_valid = int(valid_ratio * len(indices))
        train_indices = indices[num_valid:]
        valid_indices = indices[:num_valid]

        train_dataset = torch.utils.data.Subset(base_dataset, train_indices)
        valid_dataset = torch.utils.data.Subset(base_dataset, valid_indices)

    # Build the dataloaders
    train_loader = torch.utils.data.DataLoader(
        train_dataset,
        batch_size=batch_size,
        shuffle=True,
        num_workers=num_workers,
        pin_memory=use_cuda,
    )

    valid_loader = torch.utils.data.DataLoader(
        valid_dataset,
        batch_size=batch_size,
        shuffle=False,
        num_workers=num_workers,
        pin_memory=use_cuda,
    )

    return train_loader, valid_loader


def get_full_image_dataloader(data_config, use_cuda):
    img_size = (data_config["img_size"], data_config["img_size"])
    img_stride = (data_config["img_stride"], data_config["img_stride"])
    start_row = data_config["crop"]["start_row"]
    start_col = data_config["crop"]["start_col"]
    end_row = data_config["crop"]["end_row"]
    end_col = data_config["crop"]["end_col"]
    valid_ratio = data_config["valid_ratio"]
    batch_size = data_config["batch_size"]
    num_workers = data_config["num_workers"]
    name_dataset = data_config["dataset"]["name"]
    trainpath = data_config["dataset"]["trainpath"]

    logging.info("  - Dataset creation")

    input_transform = LogAmplitudeTransform(data_config["characteristics"])

    if data_config["dataset"]["name"] == "Bretigny":
        train_dataset = eval(
            f"{data_config['dataset']['name']}(root=trainpath, fold='train', transform=input_transform, patch_size=img_size, patch_stride=img_stride)"
        )
        valid_dataset = eval(
            f"{data_config['dataset']['name']}(root=trainpath, fold='valid', transform=input_transform, patch_size=img_size, patch_stride=img_stride)"
        )
        logging.info(f"  - I loaded {len(train_dataset) + len(valid_dataset)} samples")

    else:
        if data_config["dataset"]["name"] == "ALOSDataset":
            trainpath = pathlib.Path(trainpath) / "VOL-ALOS2044980750-150324-HBQR1.1__A"
            base_dataset = eval(
                f"{name_dataset}(volpath=trainpath, transform=input_transform, crop_coordinates=((start_row, start_col), (end_row, end_col)), patch_size=img_size, patch_stride=img_stride)"
            )
        elif data_config["dataset"]["name"] == "PolSFDataset":
            base_dataset = eval(
                f"{data_config['dataset']['name']}(root=trainpath, transform=input_transform, patch_size=img_size, patch_stride=img_stride)"
            )
        logging.info(f"  - I loaded {len(base_dataset)} samples")

    # Build the dataloaders
    data_loader = torch.utils.data.DataLoader(
        base_dataset,
        batch_size=batch_size,
        shuffle=False,
        num_workers=num_workers,
        pin_memory=use_cuda,
    )

    return data_loader


def reassemble_image(
    segments,
    nb_cols,
    nb_rows,
    num_channels,
    segment_size,
):
    """
    Reassembles the image segments back into a single image, starting with the rows.

    Args:
    - segments: A list of 3D tensors representing image segments.
    - n_cols: The number of columns in the original image.
    - n_rows: The number of rows in the original image.
    - num_channels: The number of channels in the image.
    - segment_size: The size of each segment (default is 128x128).

    Returns:
    - A 3D tensor representing the reassembled image.
    """
    # Correct the shape of the reassembled image to match typical (height, width, channels) format
    reassembled_image = np.zeros((num_channels, nb_rows, nb_cols), dtype=np.complex64)
    segment_index = 0
    for h in range(0, nb_rows, segment_size):
        for w in range(0, nb_cols, segment_size):
            if h + segment_size <= nb_rows and w + segment_size <= nb_cols:
                # Adjust indexing to correctly place segments based on row and column
                reassembled_image[:, h : h + segment_size, w : w + segment_size] = (
                    segments[segment_index]
                )
                segment_index += 1

    return [reassembled_image]


# The function call is commented out to prevent execution in this environment.
# segments = [np.random.rand(128, 128, 3) for _ in range(10)]  # Example segment list
# reassembled_image = reassemble_image(segments, 1024, 768, 3, 128)


def delete_folders_with_few_pngs(log_path, min_png_count=20):
    """
    Deletes folders under `root_path` containing fewer than `min_png_count` .png files.

    :param root_path: Path to the directory to search through.
    :param min_png_count: Minimum number of .png files a folder must contain to be kept.
    """

    for folder_name in os.listdir(log_path):
        folder_path = os.path.join(log_path, folder_name)
        if os.path.isdir(folder_path):  # Check if it's a directory
            png_files = [
                file for file in os.listdir(folder_path) if file.endswith(".png")
            ]
            if len(png_files) < min_png_count:
                print(
                    f"Deleting folder: {folder_path} (contains {len(png_files)} .png files)"
                )
                shutil.rmtree(folder_path)
