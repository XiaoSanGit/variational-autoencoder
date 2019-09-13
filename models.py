import tensorflow as tf
import numpy as np
import input_data
import matplotlib.pyplot as plt
import os
from scipy.misc import imsave as ims
from utils import *
from ops import *
import pandas as pd
import numpy as np
from config import hparams

class LatentAttention():
    def __init__(self):
        self.mnist = input_data.read_data_sets("MNIST_data/", one_hot=True)
        self.n_samples = self.mnist.train.num_examples

        self.n_hidden = hparams.n_hidden
        self.n_z = hparams.n_z
        self.batchsize = hparams.batch_size
        self.train_data_path = hparams.train_dataset
        self.val_data_path = hparams.val_dataset

        self.logger = Logger()
        if hparams.logger_name:
            self.logger.addHandler('file', file_path=f'{hparams.summary_path}/{hparams.logger_name}')
            # logging.info(config.memo)
        else:
            self.logger.addHandler('file', file_path=f'{hparams.summary_path}/logger.txt')

        self.feature_len = hparams.module_features_len

        # following is the model original

        # self.images = tf.placeholder(tf.float32, [None, 784])
        # image_matrix = tf.reshape(self.images,[-1, 28, 28, 1])
        # z_mean, z_stddev = self.recognition(image_matrix)
        # samples = tf.random_normal([self.batchsize,self.n_z],0,1,dtype=tf.float32)
        # guessed_z = z_mean + (z_stddev * samples)
        #
        # self.generated_images = self.generation(guessed_z)
        # generated_flat = tf.reshape(self.generated_images, [self.batchsize, 28*28])
        #
        # self.generation_loss = -tf.reduce_sum(self.images * tf.log(1e-8 + generated_flat) + (1-self.images) * tf.log(1e-8 + 1 - generated_flat),1)
        #
        # self.latent_loss = 0.5 * tf.reduce_sum(tf.square(z_mean) + tf.square(z_stddev) - tf.log(tf.square(z_stddev)) - 1,1)
        # self.cost = tf.reduce_mean(self.generation_loss + self.latent_loss)
        # self.optimizer = tf.train.AdamOptimizer(0.001).minimize(self.cost)

    def print(self, s, level=0):
        self.logger.prompt(s, level)

    def prepare_before_train(self,mode="train"):
        if mode=="train":
            self.train_iterator = self.construct_datasets(self.train_data_path,self.batchsize,do_norm=True)
            self.predict, self.loss = self.build_model(self.train_iterator)
        else:
            self.val_iterator = self.construct_datasets(train=False)
            self.build_model(train=False)



    def construct_datasets(self,path2dataset,batch_size,do_norm = True,train=True):
        self.print("constructing dataset!")

        def gen():
            # index file, format: ['path2demand_feature_file',path2develop_feature_file]
            # TODO maybe some feature added ,and the num of modules should be same.

            # TODO now features dims is same because of the simple model.
            train_index = pd.read_csv(os.path.join(path2dataset,"index.csv"),header=None)
            for item in train_index.values:
                # feature file saved as .npy file for convenience
                #TODO normization of features to 0-1// Q: in the whole datasets or this sample?
                demand_f = np.load(os.path.join(path2dataset,item[0]))
                develop_f = np.load(os.path.join(path2dataset,item[1]))
                yield demand_f,develop_f

        shapes = ((None,self.feature_len),(None,self.feature_len)) #demand_feature, develop_feature
        types = (tf.float32,tf.float32)
        ds = tf.data.Dataset.from_generator(gen, output_types=types, output_shapes=shapes)
        # ds = ds.padded_batch(self.batchsize if train else batch_size,
        #                      padded_shapes=tuple([pad_shapes[k] for k in input_format]),
        #                      padding_values=tuple([pads[k] for k in input_format]),
        #                      drop_remainder=True)
        ds = ds.shuffle(buffer_size=30)
        return ds.make_initializable_iterator()

    def build_model(self,iterator,train=True,reuse=False):
        self.print("building model...")
        def _variable_on_cpu(name, shape=None, initializer=None, dtype=None, trainable=None):
            with tf.device("/cpu:0"):
                var = tf.get_variable(name, shape=shape, initializer=initializer, dtype=dtype, trainable=trainable)
            # var = tf.get_variable(name, shape, initializer=initializer, dtype=dtype)
            return var
        self.global_step = global_step = _variable_on_cpu('global_step', shape=(),
                                                          initializer=tf.constant_initializer(0.), trainable=False)
        # TODO can use decay lr
        # self.lr_ph = tf.placeholder(tf.float32, shape=(), name='learning_rate')
        self.lr_ph = tf.train.polynomial_decay(hparams.init_lr, global_step,
                                               decay_steps=hparams.n_epochs * hparams.epoch_iters,
                                               end_learning_rate=hparams.end_lr, power=2., cycle=False)
        self.advance_global_step = tf.assign_add(global_step, 1, name='global_step_advance')
        self.optimizer = tf.train.AdamOptimizer(learning_rate=self.lr_ph)
        with tf.device("/gpu:0"):
            inputs = iterator.get_next()
            # dem_f, dev_f = inputs # b,?,channels
            with tf.variable_scope("encoder", reuse=reuse):
                h2 = self.feature_extractor(inputs)
                h2_flat = tf.reshape(h2, [self.batchsize, -1])
                c_l = h2_flat.get_shape().as_list()[-1]
                z_mean = dense(h2_flat, c_l, self.n_z, "w_mean")
                z_stddev = dense(h2_flat, c_l, self.n_z, "w_stddev")
                

            with tf.variable_scope("decoder",reuse=reuse):
                samples = tf.random_normal([self.batchsize, self.n_z], 0, 1, dtype=tf.float32)
                guessed_z = z_mean + (z_stddev * samples)
                self.generated_vector = self.feature_decoder(guessed_z)
            # TODO build the model following

    def feature_extractor(self,inputs,p=1):
        # TODO maybe we can stack the different phrase of software development into a 2-D map and us 2-D conv
        with tf.variable_scope("feature_extractor"):
            #so, normally how much modules for a software
            pre_pros = []
            ci = inputs.get_shape().as_list()[-1]
            for input_part in inputs:
                #low-dim extractor
                for idx in range(p):
                    unit_name = "pre_res_{}".format(idx + 1)
                    pre_pros.append(residual_unit(input_part, ci, ci, unit_name))
            combined_f = tf.concat(pre_pros,axis=-1) #combine module features of all phrase in software ,maybe can make it 3-D feature
            c = combined_f.get_shape().as_list()[-1]
            combined_f = tf.layers.conv1d(combined_f,c,1,padding="SAME",data_format="NWC",activation=tf.nn.leaky_relu)

            h1 = lrelu(residual_unit(combined_f,ci=c,co=c*2,k=5,stride=3,name="encoder_h1")) # b,?,c -> b,~?/3,c*2
            h2 = lrelu(residual_unit(h1, ci=c*2, co=c*4, k=3,stride=2, name="encoder_h2"))  # b,?/3,c*2 -> b,?/6,c*4
            return h2

    def feature_decoder(self,inputs):
        pass
    # encoder
    def recognition(self, input_images):
        with tf.variable_scope("recognition"):
            h1 = lrelu(conv2d(input_images, 1, 16, "d_h1")) # 28x28x1 -> 14x14x16
            h2 = lrelu(conv2d(h1, 16, 32, "d_h2")) # 14x14x16 -> 7x7x32
            h2_flat = tf.reshape(h2,[self.batchsize, 7*7*32])

            w_mean = dense(h2_flat, 7*7*32, self.n_z, "w_mean")
            w_stddev = dense(h2_flat, 7*7*32, self.n_z, "w_stddev")

        return w_mean, w_stddev

    # decoder
    def generation(self, z):
        with tf.variable_scope("generation"):
            z_develop = dense(z, self.n_z, 7*7*32, scope='z_matrix')
            z_matrix = tf.nn.relu(tf.reshape(z_develop, [self.batchsize, 7, 7, 32]))
            h1 = tf.nn.relu(conv_transpose(z_matrix, [self.batchsize, 14, 14, 16], "g_h1"))
            h2 = conv_transpose(h1, [self.batchsize, 28, 28, 1], "g_h2")
            h2 = tf.nn.sigmoid(h2)

        return h2

    def train(self):
        visualization = self.mnist.train.next_batch(self.batchsize)[0]
        reshaped_vis = visualization.reshape(self.batchsize,28,28)
        ims("results/base.jpg",merge(reshaped_vis[:64],[8,8]))
        # train
        saver = tf.train.Saver(max_to_keep=2)
        with tf.Session() as sess:
            sess.run(tf.initialize_all_variables())
            for epoch in range(10):
                for idx in range(int(self.n_samples / self.batchsize)):
                    batch = self.mnist.train.next_batch(self.batchsize)[0]
                    _, gen_loss, lat_loss = sess.run((self.optimizer, self.generation_loss, self.latent_loss), feed_dict={self.images: batch})
                    # dumb hack to print cost every epoch
                    if idx % (self.n_samples - 3) == 0:
                        print("epoch %d: genloss %f latloss %f" % (epoch, np.mean(gen_loss), np.mean(lat_loss)))
                        saver.save(sess, os.getcwd()+"/training/train",global_step=epoch)
                        generated_test = sess.run(self.generated_images, feed_dict={self.images: visualization})
                        generated_test = generated_test.reshape(self.batchsize,28,28)
                        ims("results/"+str(epoch)+".jpg",merge(generated_test[:64],[8,8]))