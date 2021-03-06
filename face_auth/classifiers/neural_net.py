import numpy as np
import logging
import os
import tensorflow as tf
from math import sqrt
from random import sample, choice, shuffle
from typing import Optional, List, Tuple
from progress.bar import Bar
from imgaug import augmenters as ia
from sklearn.metrics import roc_auc_score

from classifiers.classification_results import ClassificationResults
from common.db_helper import DB_LOCATION
from common.constants import NUM_CLASSES
from common.tools import show_image

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'


class NeuralNet:
    # History of accuracies on train set
    accs = []

    # History of accuracies on test set
    val_accs = []

    # Image augmenters
    augmenters = [
        ia.Noop(),
        ia.CoarseSaltAndPepper(p=0.2, size_percent=0.30),
        ia.CoarseSaltAndPepper(p=0.4, size_percent=0.30),
        ia.Pad(px=(3, 0, 0, 0)),
        ia.Pad(px=(0, 3, 0, 0)),
        ia.Pad(px=(0, 0, 3, 0)),
        ia.Pad(px=(0, 0, 0, 3)),
        ia.GaussianBlur(sigma=0.25),
        ia.GaussianBlur(sigma=0.5),
        ia.GaussianBlur(sigma=1),
        ia.GaussianBlur(sigma=2),
        ia.Affine(rotate=-2),
        ia.Affine(rotate=2),
        ia.PiecewiseAffine(scale=0.007)
    ]

    def __init__(self,
                 experiment_name: str,
                 # Input shape
                 input_shape: Tuple[int, int, int],
                 # Mini batch size
                 mb_size: Optional = 32,
                 # Number of filters in each convolutional layer
                 filters_count: Optional[List[int]] = None,
                 # Size of kernel, common for each convolutional layer
                 kernel_size: Optional[List[int]] = None,
                 # Neurons count in each dense layer
                 dense_layers: Optional[List[int]] = None,
                 # Learning rate
                 learning_rate: float = 0.005,
                 # Number of epochs
                 nb_epochs: int = 50000,
                 # Steps per epoch. Each |steps_per_epoch| epochs net is evaluated on val set.
                 steps_per_epoch: int = 1000,
                 # Dropout after each dense layer (excluding last)
                 dropout_rate: float = 0.5,
                 # Whether or not augmentation should be performed when choosing next
                 # batch (as opposed to augmenting the entire
                 augment_on_the_fly: bool = True,
                 augmenters: Optional[List[ia.Augmenter]] = None,
                 min_label: int = 0,
                 max_label: int = NUM_CLASSES,
                 # Whether or not classification should be in binary mode. If yes,
                 # *please* provide the |positive_class| parameter.
                 binary_classification: bool = False,
                 # ID of the subject that is considered "positive" in case of
                 # binary classification.
                 positive_class: int = 0,
                 # If provided, will store checkpoints to ckpt_file
                 ckpt_file: Optional[str] = None,
                 ):
        self.experiment_name = experiment_name
        self.input_shape = input_shape
        self.mb_size = mb_size
        self.learning_rate = learning_rate
        self.nb_epochs = nb_epochs
        self.steps_per_epoch = steps_per_epoch
        self.dropout = dropout_rate
        self.augment_on_the_fly = augment_on_the_fly
        self.ckpt_file = ckpt_file
        self.binary_classification = binary_classification
        self.positive_class = positive_class
        self.num_classes = NUM_CLASSES if not binary_classification else 1
        if dense_layers is None:
            dense_layers = [32, self.num_classes]
        self.dense_layers = dense_layers
        if filters_count is None:
            filters_count = [32, 64]
        self.filters_count = filters_count
        if kernel_size is None:
            kernel_size = [5, 5]
        self.kernel_size = kernel_size
        if binary_classification:
            self._confusion_matrix = np.zeros((2, 2))
        else:
            self._confusion_matrix = np.zeros((self.num_classes, self.num_classes))
        if augmenters is not None:
            self.augmenters = augmenters

        self._get_data(range_beg=min_label, range_end=max_label)

        # Initialize logging.
        self.logger = logging.Logger("main_logger", level=logging.INFO)
        log_file = 'log.txt'
        formatter = logging.Formatter(
            fmt='{levelname:<7} {message}',
            style='{'
        )
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)
        self.logger.addHandler(file_handler)

    def _augment_single_input(self, inp_x: np.ndarray):
        """
        Augments single input with given augmenter.
        :param inp_x: single input
        :return: augmented input
        """
        augmenter = choice(self.augmenters)
        inp_x = inp_x.reshape([1] + list(inp_x.shape))
        augmented = np.ndarray.astype(augmenter.augment_images(np.ndarray.astype(inp_x * 256, np.uint8)),
                                    np.float32)
        augmented = augmented * (1 / 256)
        augmented = augmented.reshape(inp_x.shape[1:])
        return augmented

    def _augment_train_set(self) -> None:
        """
        Augments entire training set with all augmenters.
        :return: None, appends augmented images to the train set.
        """
        train_augs = []
        for augmenter in self.augmenters:
            cur_aug = np.ndarray.astype(augmenter.augment_images(np.ndarray.astype(self.x_train * 256, np.uint8)),
                                        np.float32)
            cur_aug = cur_aug * (1 / 256)
            # Display augmented input, if you want
            # show_image(cur_aug[0].reshape(NN_INPUT_SIZE))
            train_augs.append(cur_aug)
        self.x_train = np.concatenate([self.x_train] + train_augs)
        self.y_train = np.concatenate([self.y_train] * (1 + len(train_augs)))

    def _get_data(self, range_beg: int = 0, range_end: int = 52) -> None:
        """
        :param range_beg, range_end: only samples such that label \in [range_beg, range_end) will be
            used. Sensible values for (range_beg, range_end) would be:
            * 00, 52 -> to use eurecom only
            * 52, 78 -> to use ias_lab_rgbd_only
            * 78, 98 -> to use superface_dataset only
        :return: self.(x|y)_(train|test) are set as a result
        """

        # Load stored numpy arrays from files.
        logging.info("Loading data..")
        self.x_train = np.load(DB_LOCATION + '/gen/' + self.experiment_name + '_X_train.npy')
        self.y_train = np.load(DB_LOCATION + '/gen/' + self.experiment_name + '_Y_train.npy')
        self.x_test = np.load(DB_LOCATION + '/gen/' + self.experiment_name + '_X_test.npy')
        self.y_test = np.load(DB_LOCATION + '/gen/' + self.experiment_name + '_Y_test.npy')
        train_indices = []
        test_indices = []

        # Filter out samples out of [range_beg, range_end).
        for i in range(len(self.y_train)):
            if range_end > np.argmax(self.y_train[i]) >= range_beg:
                train_indices.append(i)
        for i in range(len(self.y_test)):
            if range_end > np.argmax(self.y_test[i]) >= range_beg:
                test_indices.append(i)
        shuffle(train_indices)
        self.x_train = self.x_train[train_indices]
        self.y_train = self.y_train[train_indices]
        self.x_test = self.x_test[test_indices]
        self.y_test = self.y_test[test_indices]

        if self.binary_classification:
            def to_binary(row):
                return np.array([1.]) if np.argmax(row) == self.positive_class else np.array([0.])
            self.y_train = np.apply_along_axis(to_binary, 1, self.y_train)
            self.y_test = np.apply_along_axis(to_binary, 1, self.y_test)

        # Show first input if you want
        show_image(self.x_train[0].reshape([self.input_shape[0], self.input_shape[1] * self.input_shape[2]]))

        # Image augmentation.
        if not self.augment_on_the_fly:
            self._augment_train_set()

        logging.info("Loaded data..")

    def _visualize_kernels(self):
        """
        For each convolutional layer, visualizes filters and convolved images.
        """
        for layer_no in range(len(self.conv_layers)):
            num_filters = self.filters_count[layer_no]
            kernels = []
            applied_kernels = []
            for filter_no in range(num_filters):
                inp_x = self.input_shape[0] // (2 ** layer_no)
                inp_y = self.input_shape[1] // (2 ** layer_no)
                if layer_no == 0:
                    tmp_str = 'conv2d/kernel:0'
                else:
                    tmp_str = 'conv2d_%d/kernel:0' % layer_no
                kernel = [v for v in tf.global_variables() if v.name == tmp_str][0]
                kernel = kernel[:, :, :, filter_no]
                cur_conv_layer = self.conv_layers[layer_no]
                if layer_no == 0:
                    kernel = tf.reshape(kernel, [1, self.kernel_size[0] * self.input_shape[-1], self.kernel_size[1], 1])
                else:
                    kernel = tf.reshape(kernel, [1] +\
                                        [self.kernel_size[0] * self.filters_count[layer_no - 1], self.kernel_size[1]] +
                                        [1])
                kernels.append(kernel)
                applied = tf.reshape(cur_conv_layer[0, :, :, filter_no], [1, inp_x, inp_y, 1])
                tf.summary.image('conv{0}_filter{1}_kernel'.format(layer_no, filter_no),
                                 kernel,
                                 family='kernels_layer{0}'.format(layer_no),
                                 max_outputs=1)
                tf.summary.image('conv{0}_filter{1}_applied'.format(layer_no, filter_no),
                                 applied,
                                 family='convolved_layer_{0}'.format(layer_no),
                                 max_outputs=1)
                applied_kernels.append(applied)
            # Write concatenated patches to summary.
            concatenated_kernels = tf.concat(kernels, axis=2)
            kernels_name = "kernels_layer{0}".format(layer_no)
            tf.summary.image(kernels_name,
                             concatenated_kernels,
                             family='kernels_all_layers')
            concatenated_applieds = tf.concat(applied_kernels, axis=2)
            applieds_name = "convolved_layer{0}".format(layer_no)
            tf.summary.image(applieds_name,
                             concatenated_applieds,
                             family='convolved_all_layers')

        if self.conv_layers:
            # Merge all visualizations of kernels.
            self.merged_summary = tf.summary.merge_all()

    def _visualize_exciting_patches(self):
        """
        For each convolutional layer, visualizes patches that excite each filter the most.
        """
        # Initialize fetch handles for exciting patches and their respective responses.
        self.exciting_patches = [[None] * k for k in self.filters_count]
        self.patches_responses = [[None] * k for k in self.filters_count]
        self.flattened_exciting_patches = [[None] * k for k in self.filters_count]
        self.all_exciting_patches_at_layer = [None for _ in self.filters_count]

        for layer_no in range(len(self.conv_layers)):
            num_filters = self.filters_count[layer_no]
            cur_conv_layer = self.conv_layers[layer_no]

            for filter_no in range(num_filters):
                # Find top 10 responses to current filter, in the current mini-batch.
                inp_x = self.input_shape[0] // (2 ** layer_no)
                inp_y = self.input_shape[1] // (2 ** layer_no)
                single_filtered_flattened = tf.reshape(cur_conv_layer[:, :, :, filter_no],
                                                       [self.eff_mb_size * inp_x * inp_y])
                top10_vals, top10_indices = tf.nn.top_k(single_filtered_flattened,
                                                        k=10,
                                                        sorted=True)
                top10_reshaped = tf.map_fn(lambda sxy: [sxy // (inp_x * inp_y), (sxy // inp_y) % inp_x, sxy % inp_y],
                                           top10_indices,
                                           dtype=[tf.int32, tf.int32, tf.int32])

                def safe_cut_patch(sxy, size, img, layer_no):
                    """
                    :param (sample_no, x, y)@sxy
                    :param size: size of patch to cut out
                    :param img: image to cut it from
                    :param layer_no: current layer number
                    :return: Cuts out a patch of size (|size|) located at (x, y) on
                        input #sample_no in current batch.
                    """
                    sample_no, x, y = sxy
                    x *= 2 ** layer_no
                    y *= 2 ** layer_no
                    pad_marg_x = size[0] // 2
                    pad_marg_y = size[1] // 2
                    padding = [[0, 0],
                               [pad_marg_x, pad_marg_x],
                               [pad_marg_y, pad_marg_y],
                               [0, 0]]
                    padded = tf.pad(img, padding)
                    return padded[sample_no, x:x + size[0], y:y + size[1], :]

                # Find patches corresponding to the top 10 responses.
                # Store patches and responses in class-visible array to be retrieved later.
                self.exciting_patches[layer_no][filter_no] = \
                    tf.map_fn(lambda sxy: safe_cut_patch(sxy,
                                                         size=(self.kernel_size[0] * (2 ** layer_no),
                                                               self.kernel_size[1] * (2 ** layer_no)),
                                                         img=tf.expand_dims(self.x[:, :, :, 0], axis=-1),
                                                         layer_no=layer_no),
                              top10_reshaped,
                              dtype=tf.float32)
                self.patches_responses[layer_no][filter_no] = top10_vals

                # Flatten and concatenate the 10 patches to 2 dimensions for visualization.
                flattened_patches_shape = [1] + \
                                          [10 * self.kernel_size[0] * (2 ** layer_no),
                                           self.kernel_size[1] * (2 ** layer_no)] + \
                                          [1]
                # Write patches to summary.
                patch_name = "exciting_patches_filter{0}".format(filter_no)
                flattened_exciting_patches = tf.reshape(self.exciting_patches[layer_no][filter_no],
                                                        flattened_patches_shape,
                                                        name=patch_name)
                self.flattened_exciting_patches[layer_no][filter_no] = flattened_exciting_patches
            self.all_exciting_patches_at_layer[layer_no] = tf.concat(self.flattened_exciting_patches[layer_no], axis=2)
            # Write concatenated patches to summary.
            all_patches_name = "exciting_patches_layer{0}".format(layer_no)
            tf.summary.image(all_patches_name,
                             self.all_exciting_patches_at_layer[layer_no],
                             family='exciting_all_layers')

            # Merge all summaries.
            self.merged_summary = tf.summary.merge_all()

    def _visualize_incorrect_answer_images(self):
        correct = tf.boolean_mask(self.x, self.correct)
        correct = tf.transpose(correct, perm=[0, 1, 3, 2])
        correct = tf.reshape(correct, shape=[1, -1, self.input_shape[1] * self.input_shape[2], 1])
        correct = tf.concat([correct, tf.zeros(shape=[1, 1, self.input_shape[1] * self.input_shape[2], 1])], axis=1)
        tf.summary.image('correct', correct)
        incorrect = tf.boolean_mask(self.x, tf.logical_not(self.correct))
        incorrect = tf.transpose(incorrect, perm=[0, 1, 3, 2])
        incorrect = tf.reshape(incorrect, shape=[1, -1, self.input_shape[1] * self.input_shape[2], 1])
        incorrect = tf.concat([incorrect, tf.zeros(shape=[1, 1, self.input_shape[1] * self.input_shape[2], 1])], axis=1)
        tf.summary.image('incorrect', incorrect)

        # Merge all summaries.
        self.merged_summary = tf.summary.merge_all()

    def _create_convolutional_layers(self) -> None:
        signal = self.x

        for layer_no in range(len(self.filters_count)):
            num_filters = self.filters_count[layer_no]
            signal = tf.layers.batch_normalization(signal)

            # Init weights with std.dev = sqrt(2 / N)
            #
            input_size = int(signal.get_shape()[1]) * int(signal.get_shape()[2]) * int(signal.get_shape()[3])
            w_init = tf.initializers.random_normal(stddev=sqrt(2 / input_size))

            # Convolutional layer
            cur_conv_layer = tf.layers.conv2d(inputs=signal,
                                              filters=num_filters,
                                              kernel_size=self.kernel_size,
                                              kernel_initializer=w_init,
                                              padding='same')

            # Reduce image dimensions in half.
            cur_pool_layer = tf.layers.max_pooling2d(inputs=cur_conv_layer,
                                                     pool_size=[2, 2],
                                                     strides=2,
                                                     padding='valid')

            self.conv_layers.append(cur_conv_layer)
            self.pool_layers.append(cur_pool_layer)

            # Set pooled image as current signal
            signal = cur_pool_layer

        return signal

    def _create_dense_layers(self) -> None:
        signal = self.x if not self.pool_layers else self.pool_layers[-1]
        input_size = int(signal.get_shape()[1]) * int(signal.get_shape()[2]) * int(signal.get_shape()[3])
        signal = tf.reshape(signal, [self.eff_mb_size, input_size])

        for num_neurons in self.dense_layers[:-1]:
            signal = tf.layers.batch_normalization(signal)

            # Init weights with std.dev = sqrt(2 / N)
            # https://www.cv-foundation.org/openaccess/content_iccv_2015/papers/He_Delving_Deep_into_ICCV_2015_paper.pdf?spm=5176.100239.blogcont55892.28.pm8zm1&file=He_Delving_Deep_into_ICCV_2015_paper.pdf
            input_size = int(signal.get_shape()[1])
            w_init = tf.initializers.random_normal(stddev=sqrt(2 / input_size))

            cur_dense_layer = tf.layers.dense(inputs=signal,
                                              units=num_neurons,
                                              activation=tf.nn.leaky_relu,
                                              kernel_initializer=w_init)

            signal = cur_dense_layer

            # Apply dropout
            cur_dropout_layer = tf.layers.dropout(inputs=signal,
                                                  rate=self.dropout)

            signal = cur_dropout_layer

        # Init weights with std.dev = sqrt(2 / N)
        input_size = int(signal.get_shape()[1])
        w_init = tf.initializers.random_normal(stddev=tf.sqrt(tf.constant(2.) / input_size))
        cur_layer = tf.layers.dense(inputs=signal,
                                    activation=tf.nn.sigmoid,
                                    units=self.dense_layers[-1],
                                    kernel_initializer=w_init)
        self.output_layer = cur_layer

    def _create_training_objectives(self) -> None:
        if self.binary_classification:
            self.preds = tf.cast(tf.round(self.output_layer), dtype=tf.int64)
            self.y_sparse = tf.cast(self.y, dtype=tf.int64)
        else:
            self.preds = tf.argmax(self.output_layer, axis=1)
            self.y_sparse = tf.argmax(self.y, axis=1)
        self.loss = tf.losses.log_loss(self.y, self.output_layer)
        self.correct = tf.reshape(tf.equal(self.y_sparse, self.preds), shape=[self.eff_mb_size])
        self.accuracy = tf.reduce_mean(tf.cast(self.correct, tf.float32))
        self.train_op = tf.train.GradientDescentOptimizer(self.learning_rate).minimize(self.loss)

        self.logger.info('list of variables {0}'.format(list(map(lambda x: x.name, tf.global_variables()))))

    def _create_model(self):
        self.x = tf.placeholder(dtype=tf.float32, shape=[None] + list(self.input_shape))
        self.y = tf.placeholder(dtype=tf.float32, shape=[None, self.num_classes])
        self.eff_mb_size = tf.shape(self.x)[0]  # Effective batch size
        self.conv_layers = []
        self.pool_layers = []

        self._create_convolutional_layers()
        self._create_dense_layers()
        self._create_training_objectives()

    def train_on_batch(self, batch_x, batch_y):
        """
        :return: [loss, accuracy]
        """
        results = self.sess.run([self.loss, self.accuracy, self.train_op],
                                feed_dict={self.x: batch_x, self.y: batch_y})
        self.accs.append(results[1])
        return results[:2]

    def test_on_batch(self, batch_x, batch_y, global_step=1) -> Tuple[float, float, List[float]]:
        """
        Note that this function does not fetch |self.train_op|, so that the weights
        are not updated.
        :param batch_x:
        :param batch_y:
        :param global_step:
        :return: (loss, accuracy, probs)
        """
        if self.conv_layers:
            # Write summary
            results = self.sess.run([self.loss, self.accuracy, self.output_layer, self.preds, self.merged_summary],
                                    feed_dict={self.x: batch_x, self.y: batch_y})
            msum = results[4]
            self.writer.add_summary(msum, global_step=global_step)
            self.writer.flush()
        else:
            results = self.sess.run([self.loss, self.accuracy, self.preds],
                                    feed_dict={self.x: batch_x, self.y: batch_y})
        self.val_accs.append(results[1])
        # Update confusion matrix
        preds = results[3]
        for i in range(len(batch_x)):
            self._confusion_matrix[np.argmax(batch_y[i]), preds[i]] += 1.

        return results[0], results[1], list(results[2])

    def validate(self, global_step) -> ClassificationResults:
        """
        :return: (loss, accuracy, auc_roc)
        Note that if self.binary_classification is False, auc_roc may be anything
        """
        losses = []
        accs = []
        all_pred_probs = []
        all_labels = []
        for batch_no in range(self.x_test.shape[0] // self.mb_size + 1):
            inputs = self.x_test[batch_no * self.mb_size: (batch_no+1) * self.mb_size]
            labels = self.y_test[batch_no * self.mb_size: (batch_no+1) * self.mb_size]
            loss, acc, probs = self.test_on_batch(inputs, labels, global_step=global_step)
            losses.append(loss)
            accs.append(acc)
            all_pred_probs += probs
            all_labels += list(labels)
        all_pred_probs = np.array(all_pred_probs)
        all_labels = np.array(all_labels)
        all_labels = all_labels.astype(dtype=np.bool)
        loss = np.mean(losses)
        acc = np.mean(accs)
        return ClassificationResults(loss=loss, acc=acc, pred_probs=all_pred_probs, labels=all_labels,
                                     binary=self.binary_classification)

    def _next_training_batch(self) -> (np.ndarray, np.ndarray):
        batch = sample(list(range(self.x_train.shape[0])), self.mb_size)
        batch_x, batch_y = self.x_train[batch], self.y_train[batch]
        if self.augment_on_the_fly:
            for sample_no in range(self.mb_size):
                batch_x[sample_no] = self._augment_single_input(batch_x[sample_no])
        return batch_x, batch_y

    def train_and_evaluate(self) -> ClassificationResults:
        """
        Train and evaluate model.
        """
        with tf.Session() as self.sess:
            # Initialize computation graph.
            self._create_model()
            # Add visualizations to computation graph.
            self._visualize_kernels()
            self._visualize_exciting_patches()
            self._visualize_incorrect_answer_images()

            # Initialize variables.
            if self.ckpt_file:
                saver = tf.train.Saver()
                try:
                    saver.restore(self.sess, self.ckpt_file)
                except (tf.errors.InvalidArgumentError, tf.errors.NotFoundError):
                    tf.global_variables_initializer().run()
            else:
                tf.global_variables_initializer().run()

            # Initialize summary writer.
            self.writer = tf.summary.FileWriter(logdir='conv_vis')

            # Initialize progress bar.
            bar = Bar('', max=self.steps_per_epoch, suffix='%(index)d/%(max)d ETA: %(eta)ds')

            for epoch_no in range(self.nb_epochs):
                self.logger.info("Epoch {epoch_no}/{nb_epochs}".format(epoch_no=epoch_no,
                                                                       nb_epochs=self.nb_epochs))
                for step_no in range(self.steps_per_epoch):
                    # Train model on next batch
                    batch_x, batch_y = self._next_training_batch()
                    results = self.train_on_batch(batch_x, batch_y)

                    # Update bar
                    bar.message = 'loss: {0[0]:.8f} acc: {0[1]:.3f} mean_acc: {1:.3f}'. \
                        format(results, np.mean(self.accs[-1000:]), )
                    bar.next()

                # Re-initialize progress bar
                bar.finish()
                bar = Bar('', max=self.steps_per_epoch, suffix='%(index)d/%(max)d ETA: %(eta)ds')

                # Store model
                if self.ckpt_file:
                    saver.save(self.sess, self.ckpt_file)

                # Validate
                val_results = self.validate(global_step=epoch_no)
                loss, acc, auc_roc = val_results.loss, val_results.acc, val_results.get_auc_roc()
                if self.binary_classification:
                    self.logger.info("Validation results: Loss: {0}, accuracy: {1}, auc_roc: {2}".
                                     format(loss, acc, auc_roc))
                else:
                    self.logger.info("Validation results: Loss: {0}, accuracy: {1}".format(loss, acc))
                # Dipslay confusion matrix
                show_image(self._confusion_matrix)

            return val_results
