"""
Preprocessing the input images is very expensive, as we want to crop them to
faces and calculate entropy and (TODO) HOGs of entropy maps.
Use this script to generate (X|Y)_(train|test).npy files and load them directly
in main.py.
"""

from common.db_helper import DBHelper, Database, DB_LOCATION
import numpy as np
from common.constants import IMG_SIZE
from skimage.filters.rank import entropy
from skimage.morphology import disk
import os
import logging


def build_input_vector(gird_face):
    """ Concatenates: grey_or_ir_face, depth_face, entr_grey_or_ir_face, entr_depth_face"""
    (gir_face, depth_face) = gird_face
    if gir_face is None or depth_face is None:
        return None
    tmp = np.zeros((4 * IMG_SIZE, IMG_SIZE))
    entr_gir_face = entropy(gir_face, disk(5))
    entr_gir_face = entr_gir_face / np.max(entr_gir_face)
    entr_depth_face = entropy(depth_face, disk(5))
    entr_depth_face = entr_depth_face / np.max(entr_depth_face)
    tmp[0:IMG_SIZE] = depth_face
    tmp[IMG_SIZE:IMG_SIZE * 2] = gir_face
    tmp[IMG_SIZE * 2:IMG_SIZE * 3] = entr_gir_face
    tmp[IMG_SIZE * 3:IMG_SIZE * 4] = entr_depth_face
    return tmp


def load_database(database, offset, override_test_set=False):
    logging.debug('Loading database %s' % database.get_name())
    for i in range(database.subjects_count()):
        logging.debug('Subject', i)
        for j in range(database.imgs_per_subject(i)):
            logging.debug('Photo %d/%d' % (j, database.imgs_per_subject(i)))
            x = build_input_vector(database.load_gird_face(i, j))
            y = offset + i + 1
            if x is None or y is None:
                continue
            if database.is_photo_in_test_set(i, j):
                x_test.append(x)
                y_test.append(y)
            else:
                x_train.append(x)
                y_train.append(y)


if __name__ == '__main__':
    # Load data
    x_train = []
    y_train = []
    x_test = []
    y_test = []

    helper = DBHelper()
    TOTAL_SUBJECTS_COUNT = helper.all_subjects_count()
    logging.debug(TOTAL_SUBJECTS_COUNT)

    sum_offset = 0
    for database in helper.get_databases():
        load_database(database, sum_offset)
        sum_offset += database.subjects_count()

    # Reshape input
    TRAIN_SIZE = len(x_train)
    TEST_SIZE = len(x_test)
    X_train = np.zeros((TRAIN_SIZE, 4 * IMG_SIZE, IMG_SIZE, 1))
    Y_train = np.zeros((TRAIN_SIZE, TOTAL_SUBJECTS_COUNT))
    X_test = np.zeros((TEST_SIZE, 4 * IMG_SIZE, IMG_SIZE, 1))
    Y_test = np.zeros((TEST_SIZE, TOTAL_SUBJECTS_COUNT))
    for i in range(TRAIN_SIZE):
        X_train[i] = x_train[i].reshape((4 * IMG_SIZE, IMG_SIZE, 1))
        Y_train[i, y_train[i]-1] = 1
    for i in range(TEST_SIZE):
        X_test[i] = x_test[i].reshape((4 * IMG_SIZE, IMG_SIZE, 1))
        Y_test[i, y_test[i]-1] = 1
    x_train = []
    y_train = []
    x_test = []
    y_test = []

    for i in range(len(X_train)):
        if np.isnan(X_train[i]).any():
            # TODO: do something smarter - investigate how nan values sneaked into
            # input data. This has probably happened when computing entropy maps
            # (I think I saw a warning from numpy).
            # The invalid images were: 2871, 2873, 2874
            X_train[i] = X_train[0]
            Y_train[i] = Y_train[0]

    assert os.path.isdir(DB_LOCATION), "database directory not found"
    if not os.path.isdir(DB_LOCATION + '/gen'):
        os.makedirs(DB_LOCATION + '/gen')

    np.save(DB_LOCATION + '/gen/no_normalization_X_train', X_train)
    np.save(DB_LOCATION + '/gen/no_normalization_Y_train', Y_train)
    np.save(DB_LOCATION + '/gen/no_normalization_X_test', X_test)
    np.save(DB_LOCATION + '/gen/no_normalization_Y_test', Y_test)
