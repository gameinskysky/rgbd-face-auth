import numpy as np

from common.constants import NUM_CLASSES, DB_LOCATION
from classifiers.neural_net import NeuralNet
from experiments.no_rotation_channels_without_hogs.constants import EXP_NAME, NN_INPUT_SIZE


def run_main():
    # Test on eurecom + ias_lab_rgbd
    net = NeuralNet(experiment_name=EXP_NAME,
                    input_shape=NN_INPUT_SIZE,
                    mb_size=32,
                    kernel_size=[5, 5],
                    nb_epochs=100,
                    steps_per_epoch=1000,
                    filters_count=[20, 20, 40],
                    dense_layers=[NUM_CLASSES],
                    dropout_rate=0.5,
                    learning_rate=0.05,
                    ckpt_file='ckpts/'+EXP_NAME)
    pred_probs = net.train_and_evaluate().pred_probs
    np.save(DB_LOCATION + '/gen/' + EXP_NAME + '_pred_probs.npy', pred_probs)
    return pred_probs


if __name__ == '__main__':
    run_main()
