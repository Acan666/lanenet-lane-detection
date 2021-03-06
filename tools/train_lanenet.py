#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# @Time    : 19-4-24 下午9:33
# @Author  : MaybeShewill-CV
# @Site    : https://github.com/MaybeShewill-CV/lanenet-lane-detection
# @File    : train_lanenet.py
# @IDE: PyCharm
"""
Train lanenet script
"""
import argparse
import math
import os.path as ops
import time

import cv2
import glog as log
import numpy as np
import tensorflow as tf
import sys
sys.path.append('./')
import os
GPU_IDS = '0'
os.environ["CUDA_VISIBLE_DEVICES"] = GPU_IDS
from config import global_config
from data_provider import lanenet_data_feed_pipline
from lanenet_model import lanenet
from tools import evaluate_model_utils

CFG = global_config.cfg


def init_args():
    """

    :return:
    """
    parser = argparse.ArgumentParser()

    parser.add_argument('-d', '--dataset_dir', type=str,default='H:/Other_DataSets/TuSimple/',
                        help='Lanenet Dataset dir')
    parser.add_argument('-w', '--weights_path', type=str,
                        # default='./model/tusimple_lanenet_vgg/tusimple_lanenet_vgg_changename.ckpt',
                        default='./model/tusimple_lanenet_mobilenet_v2_1005/tusimple_lanenet_3600_0.929177263960692.ckpt-3601',
                        help='Path to pre-trained weights to continue training')
    parser.add_argument('-m', '--multi_gpus', type=bool, default=True,
                        help='Use multi gpus to train')
    parser.add_argument('--net_flag', type=str, default='mobilenet_v2', # mobilenet_v2 vgg
                        help='The net flag which determins the net\'s architecture')
    parser.add_argument('--version_flag', type=str, default='0403',
                        help='The net flag which determins the net\'s architecture')
    parser.add_argument('--scratch', type=bool, default=True,
                        help='Is training from scratch ?')

    return parser.parse_args()


def minmax_scale(input_arr):
    """

    :param input_arr:
    :return:
    """
    min_val = np.min(input_arr)
    max_val = np.max(input_arr)

    output_arr = (input_arr - min_val) * 255.0 / (max_val - min_val)

    return output_arr


def load_pretrained_weights(variables, pretrained_weights_path, sess):
    """

    :param variables:
    :param pretrained_weights_path:
    :param sess:
    :return:
    """
    assert ops.exists(pretrained_weights_path), '{:s} not exist'.format(pretrained_weights_path)

    pretrained_weights = np.load(
        './data/vgg16.npy', encoding='latin1').item()

    for vv in variables:
        weights_key = vv.name.split('/')[-3]
        if 'conv5' in weights_key:
            weights_key = '{:s}_{:s}'.format(weights_key.split('_')[0], weights_key.split('_')[1])
        try:
            weights = pretrained_weights[weights_key][0]
            _op = tf.assign(vv, weights)
            sess.run(_op)
        except Exception as _:
            continue

    return

# 保持训练/测试阶段的临时结果
def record_training_intermediate_result(gt_images, gt_binary_labels, gt_instance_labels,
                                        binary_seg_images, pix_embeddings, flag='train',
                                        save_dir='./tmp'):
    """
    record intermediate result during training process for monitoring
    :param gt_images:
    :param gt_binary_labels:
    :param gt_instance_labels:
    :param binary_seg_images:
    :param pix_embeddings:
    :param flag:
    :param save_dir:
    :return:
    """
    os.makedirs(save_dir, exist_ok=True)

    for index, gt_image in enumerate(gt_images):
        gt_image_name = '{:s}_{:d}_gt_image.png'.format(flag, index + 1)
        gt_image_path = ops.join(save_dir, gt_image_name)
        gt_image = (gt_images[index] + 1.0) * 127.5
        cv2.imwrite(gt_image_path, np.array(gt_image, dtype=np.uint8))

        gt_binary_label_name = '{:s}_{:d}_gt_binary_label.png'.format(flag, index + 1)
        gt_binary_label_path = ops.join(save_dir, gt_binary_label_name)
        cv2.imwrite(gt_binary_label_path, np.array(gt_binary_labels[index][:, :, 0] * 255, dtype=np.uint8))

        gt_instance_label_name = '{:s}_{:d}_gt_instance_label.png'.format(flag, index + 1)
        gt_instance_label_path = ops.join(save_dir, gt_instance_label_name)
        cv2.imwrite(gt_instance_label_path, np.array(gt_instance_labels[index][:, :, 0], dtype=np.uint8))

        gt_binary_seg_name = '{:s}_{:d}_gt_binary_seg.png'.format(flag, index + 1)
        gt_binary_seg_path = ops.join(save_dir, gt_binary_seg_name)
        cv2.imwrite(gt_binary_seg_path, np.array(binary_seg_images[index] * 255, dtype=np.uint8))

        embedding_image_name = '{:s}_{:d}_pix_embedding.png'.format(flag, index + 1)
        embedding_image_path = ops.join(save_dir, embedding_image_name)
        embedding_image = pix_embeddings[index]
        for i in range(CFG.TRAIN.EMBEDDING_FEATS_DIMS):
            embedding_image[:, :, i] = minmax_scale(embedding_image[:, :, i])
        embedding_image = np.array(embedding_image, np.uint8)
        cv2.imwrite(embedding_image_path, embedding_image)

    return


def average_gradients(tower_grads):
    """Calculate the average gradient for each shared variable across all towers.
    Note that this function provides a synchronization point across all towers.
    Args:
      tower_grads: List of lists of (gradient, variable) tuples. The outer list
        is over individual gradients. The inner list is over the gradient
        calculation for each tower.
    Returns:
       List of pairs of (gradient, variable) where the gradient has been averaged
       across all towers.
    """
    average_grads = []
    for grad_and_vars in zip(*tower_grads):
        # Note that each grad_and_vars looks like the following:
        #   ((grad0_gpu0, var0_gpu0), ... , (grad0_gpuN, var0_gpuN))
        grads = []
        for g, _ in grad_and_vars:
            # Add 0 dimension to the gradients to represent the tower.
            expanded_g = tf.expand_dims(g, 0)

            # Append on a 'tower' dimension which we will average over below.
            grads.append(expanded_g)

        # Average over the 'tower' dimension.
        grad = tf.concat(grads, 0)
        grad = tf.reduce_mean(grad, 0)

        # Keep in mind that the Variables are redundant because they are shared
        # across towers. So .. we will just return the first tower's pointer to
        # the Variable.
        v = grad_and_vars[0][1]
        grad_and_var = (grad, v)
        average_grads.append(grad_and_var)

    return average_grads


def compute_net_gradients(gt_images, gt_binary_labels, gt_instance_labels,
                          net, optimizer=None):
    """
    Calculate gradients for single GPU
    :param gt_images:
    :param gt_binary_labels:
    :param gt_instance_labels:
    :param net:
    :param optimizer:
    :return:
    """

    compute_ret = net.compute_loss(
        input_tensor=gt_images, binary_label=gt_binary_labels,
        instance_label=gt_instance_labels, name='lanenet_model'
    )
    total_loss = compute_ret['total_loss']

    if optimizer is not None:
        grads = optimizer.compute_gradients(total_loss)
    else:
        grads = None

    return total_loss, grads


def train_lanenet(dataset_dir, weights_path=None, net_flag='vgg', version_flag='', scratch=False):
    """
    Train LaneNet With One GPU
    :param dataset_dir:
    :param weights_path:
    :param net_flag:
    :param version_flag:
    :param scratch:
    :return:
    """
    train_dataset = lanenet_data_feed_pipline.LaneNetDataFeeder(
        dataset_dir=dataset_dir, flags='train'
    )
    val_dataset = lanenet_data_feed_pipline.LaneNetDataFeeder(
        dataset_dir=dataset_dir, flags='val'
    )

    # ================================================================ #
    #                           Define Network                         #
    # ================================================================ #
    train_net = lanenet.LaneNet(net_flag=net_flag, phase='train', reuse=tf.AUTO_REUSE)
    val_net = lanenet.LaneNet(net_flag=net_flag, phase='val', reuse=True)
    # ---------------------------------------------------------------- #

    # ================================================================ #
    #                       Train Input & Output                       #
    # ================================================================ #
    # set compute graph node for training
    train_images, train_binary_labels, train_instance_labels = train_dataset.inputs(
        CFG.TRAIN.BATCH_SIZE
    )

    train_compute_ret = train_net.compute_loss(
        input_tensor=train_images, binary_label=train_binary_labels,
        instance_label=train_instance_labels, name='lanenet_model'
    )
    train_total_loss = train_compute_ret['total_loss']
    train_binary_seg_loss = train_compute_ret['binary_seg_loss'] # 语义分割 loss
    train_disc_loss = train_compute_ret['discriminative_loss'] # embedding loss
    train_pix_embedding = train_compute_ret['instance_seg_logits'] # embedding feature, HxWxN
    train_l2_reg_loss = train_compute_ret['l2_reg_loss']

    train_prediction_logits = train_compute_ret['binary_seg_logits'] # 语义分割结果，HxWx2
    train_prediction_score = tf.nn.softmax(logits=train_prediction_logits)
    train_prediction = tf.argmax(train_prediction_score, axis=-1) # 语义分割二值图

    train_accuracy = evaluate_model_utils.calculate_model_precision(
        train_compute_ret['binary_seg_logits'], train_binary_labels
    )
    train_fp = evaluate_model_utils.calculate_model_fp(
        train_compute_ret['binary_seg_logits'], train_binary_labels
    )
    train_fn = evaluate_model_utils.calculate_model_fn(
        train_compute_ret['binary_seg_logits'], train_binary_labels
    )
    train_binary_seg_ret_for_summary = evaluate_model_utils.get_image_summary(
        img=train_prediction
    ) # (I - min) * 255 / (max -min), 归一化到0-255
    train_embedding_ret_for_summary = evaluate_model_utils.get_image_summary(
        img=train_pix_embedding
    ) # (I - min) * 255 / (max -min), 归一化到0-255
    # ---------------------------------------------------------------- #

    # ================================================================ #
    #                          Define Optimizer                        #
    # ================================================================ #
    # set optimizer
    global_step = tf.Variable(0, trainable=False, name='global_step')
    # learning_rate = tf.train.cosine_decay_restarts( # 余弦衰减
    #     learning_rate=CFG.TRAIN.LEARNING_RATE,      # 初始学习率
    #     global_step=global_step,                    # 当前迭代次数
    #     first_decay_steps=CFG.TRAIN.STEPS/3,        # 首次衰减周期
    #     t_mul=2.0,                                  # 随后每次衰减周期倍数
    #     m_mul=1.0,                                  # 随后每次初始学习率倍数
    #     alpha = 0.1,                                # 最小的学习率=alpha*learning_rate
    # )
    learning_rate = tf.train.polynomial_decay(  # 多项式衰减
        learning_rate=CFG.TRAIN.LEARNING_RATE,  # 初始学习率
        global_step=global_step,  # 当前迭代次数
        decay_steps=CFG.TRAIN.STEPS / 4,  # 在迭代到该次数实际，学习率衰减为 learning_rate * dacay_rate
        end_learning_rate=CFG.TRAIN.LEARNING_RATE / 10,  # 最小的学习率
        power=0.9,
        cycle=True
    )
    learning_rate_scalar = tf.summary.scalar(name='learning_rate', tensor=learning_rate)
    update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS) # for batch normalization
    with tf.control_dependencies(update_ops):
        optimizer = tf.train.MomentumOptimizer(
            learning_rate=learning_rate, momentum=CFG.TRAIN.MOMENTUM).minimize(
            loss=train_total_loss,
            var_list=tf.trainable_variables(),
            global_step=global_step
        )
    # ---------------------------------------------------------------- #

    # ================================================================ #
    #                           Train Summary                          #
    # ================================================================ #
    train_cost_scalar = tf.summary.scalar(
        name='train_cost', tensor=train_total_loss
    )
    train_accuracy_scalar = tf.summary.scalar(
        name='train_accuracy', tensor=train_accuracy
    )
    train_binary_seg_loss_scalar = tf.summary.scalar(
        name='train_binary_seg_loss', tensor=train_binary_seg_loss
    )
    train_instance_seg_loss_scalar = tf.summary.scalar(
        name='train_instance_seg_loss', tensor=train_disc_loss
    )
    train_fn_scalar = tf.summary.scalar(
        name='train_fn', tensor=train_fn
    )
    train_fp_scalar = tf.summary.scalar(
        name='train_fp', tensor=train_fp
    )
    train_binary_seg_ret_img = tf.summary.image(
        name='train_binary_seg_ret', tensor=train_binary_seg_ret_for_summary
    )
    train_embedding_feats_ret_img = tf.summary.image(
        name='train_embedding_feats_ret', tensor=train_embedding_ret_for_summary
    )
    train_merge_summary_op = tf.summary.merge(
        [train_accuracy_scalar, train_cost_scalar, train_binary_seg_loss_scalar,
         train_instance_seg_loss_scalar, train_fn_scalar, train_fp_scalar,
         train_binary_seg_ret_img, train_embedding_feats_ret_img,
         learning_rate_scalar]
    )
    # ---------------------------------------------------------------- #

    # ================================================================ #
    #                        Val Input & Output                        #
    # ================================================================ #
    # set compute graph node for validation
    val_images, val_binary_labels, val_instance_labels = val_dataset.inputs(
        CFG.TEST.BATCH_SIZE
    )

    val_compute_ret = val_net.compute_loss(
        input_tensor=val_images, binary_label=val_binary_labels,
        instance_label=val_instance_labels, name='lanenet_model'
    )
    val_total_loss = val_compute_ret['total_loss']
    val_binary_seg_loss = val_compute_ret['binary_seg_loss']
    val_disc_loss = val_compute_ret['discriminative_loss']
    val_pix_embedding = val_compute_ret['instance_seg_logits']

    val_prediction_logits = val_compute_ret['binary_seg_logits']
    val_prediction_score = tf.nn.softmax(logits=val_prediction_logits)
    val_prediction = tf.argmax(val_prediction_score, axis=-1)

    val_accuracy = evaluate_model_utils.calculate_model_precision(
        val_compute_ret['binary_seg_logits'], val_binary_labels
    )
    val_fp = evaluate_model_utils.calculate_model_fp(
        val_compute_ret['binary_seg_logits'], val_binary_labels
    )
    val_fn = evaluate_model_utils.calculate_model_fn(
        val_compute_ret['binary_seg_logits'], val_binary_labels
    )
    val_binary_seg_ret_for_summary = evaluate_model_utils.get_image_summary(
        img=val_prediction
    )
    val_embedding_ret_for_summary = evaluate_model_utils.get_image_summary(
        img=val_pix_embedding
    )
    # ---------------------------------------------------------------- #

    # ================================================================ #
    #                            VAL Summary                           #
    # ================================================================ #
    val_cost_scalar = tf.summary.scalar(
        name='val_cost', tensor=val_total_loss
    )
    val_accuracy_scalar = tf.summary.scalar(
        name='val_accuracy', tensor=val_accuracy
    )
    val_binary_seg_loss_scalar = tf.summary.scalar(
        name='val_binary_seg_loss', tensor=val_binary_seg_loss
    )
    val_instance_seg_loss_scalar = tf.summary.scalar(
        name='val_instance_seg_loss', tensor=val_disc_loss
    )
    val_fn_scalar = tf.summary.scalar(
        name='val_fn', tensor=val_fn
    )
    val_fp_scalar = tf.summary.scalar(
        name='val_fp', tensor=val_fp
    )
    val_binary_seg_ret_img = tf.summary.image(
        name='val_binary_seg_ret', tensor=val_binary_seg_ret_for_summary
    )
    val_embedding_feats_ret_img = tf.summary.image(
        name='val_embedding_feats_ret', tensor=val_embedding_ret_for_summary
    )
    val_merge_summary_op = tf.summary.merge(
        [val_accuracy_scalar, val_cost_scalar, val_binary_seg_loss_scalar,
         val_instance_seg_loss_scalar, val_fn_scalar, val_fp_scalar,
         val_binary_seg_ret_img, val_embedding_feats_ret_img]
    )
    # ---------------------------------------------------------------- #

    # ================================================================ #
    #                      Config Saver & Session                      #
    # ================================================================ #
    # Set tf model save path
    model_save_dir = 'model/tusimple_lanenet_{:s}_{:s}'.format(net_flag, version_flag)
    os.makedirs(model_save_dir, exist_ok=True)
    train_start_time = time.strftime('%Y-%m-%d-%H-%M-%S', time.localtime(time.time()))
    model_name = 'tusimple_lanenet_{:s}_{:s}.ckpt'.format(net_flag, str(train_start_time))
    model_save_path = ops.join(model_save_dir, model_name)

    # ==============================
    if scratch:
        """
        删除 Momentum 的参数, 注意这里保存的 meta 文件也会删了
        tensorflow 在 save model 的时候，如果选择了 global_step 选项，会 global_step 值也保存下来，
        然后 restore 的时候也就会接着这个 global_step 继续训练下去，因此需要去掉
        """
        variables = tf.contrib.framework.get_variables_to_restore()
        variables_to_resotre = [v for v in variables if 'Momentum' not in v.name.split('/')[-1]]
        variables_to_resotre = [v for v in variables_to_resotre if 'global_step' not in v.name.split('/')[-1]]
        restore_saver = tf.train.Saver(variables_to_resotre)
    else:
        restore_saver = tf.train.Saver()
    saver = tf.train.Saver(max_to_keep=10)
    # ==============================

    # Set tf summary save path
    tboard_save_path = 'tboard/tusimple_lanenet_{:s}_{:s}'.format(net_flag, version_flag)
    os.makedirs(tboard_save_path, exist_ok=True)

    # Set sess configuration
    # ============================== config GPU
    sess_config = tf.ConfigProto(allow_soft_placement=True)
    sess_config.gpu_options.per_process_gpu_memory_fraction = CFG.TRAIN.GPU_MEMORY_FRACTION
    sess_config.gpu_options.allow_growth = CFG.TRAIN.TF_ALLOW_GROWTH
    sess_config.gpu_options.allocator_type = 'BFC'
    # ==============================
    sess = tf.Session(config=sess_config)

    summary_writer = tf.summary.FileWriter(tboard_save_path)
    summary_writer.add_graph(sess.graph)
    # ---------------------------------------------------------------- #

    # Set the training parameters
    import math
    train_steps = CFG.TRAIN.STEPS
    val_steps = math.ceil(CFG.TRAIN.VAL_SIZE / CFG.TEST.BATCH_SIZE) # 测试一个 epoch 需要的 batch 数量
    one_epoch2step = math.ceil(CFG.TRAIN.TRAIN_SIZE / CFG.TRAIN.BATCH_SIZE) # 训练一个 epoch 需要的 batch 数量

    log.info('Global configuration is as follows:')
    log.info(CFG)
    max_acc = 0.9
    save_num = 0
    # ================================================================ #
    #                            Train & Val                           #
    # ================================================================ #
    with sess.as_default():
        # ============================== load pretrain model
        if weights_path is None:
            log.info('Training from scratch')
            sess.run(tf.global_variables_initializer())
        elif net_flag == 'vgg' and weights_path is None:
            load_pretrained_weights(tf.trainable_variables(), './data/vgg16.npy', sess)
        elif scratch: # 从头开始训练，类似 Caffe 的 --weights
            sess.run(tf.global_variables_initializer())
            log.info('Restore model from last model checkpoint {:s}, scratch'.format(weights_path))
            try:
                restore_saver.restore(sess=sess, save_path=weights_path)
            except:
                log.info('model maybe is not exist!')
        else: # 继续训练，类似 Caffe 的 --snapshot
            log.info('Restore model from last model checkpoint {:s}'.format(weights_path))
            try:
                restore_saver.restore(sess=sess, save_path=weights_path)
            except:
                log.info('model maybe is not exist!')
        # ==============================

        train_cost_time_mean = [] # 统计一个 batch 训练耗时
        for step in range(train_steps):
            # ================================================================ #
            #                               Train                              #
            # ================================================================ #
            t_start = time.time()

            _, train_loss, train_accuracy_figure, train_fn_figure, train_fp_figure, \
                lr, train_summary, train_binary_loss, \
                train_instance_loss, train_embeddings, train_binary_seg_imgs, train_gt_imgs, \
                train_binary_gt_labels, train_instance_gt_labels, train_l2_loss = \
                sess.run([optimizer, train_total_loss, train_accuracy, train_fn, train_fp,
                          learning_rate, train_merge_summary_op, train_binary_seg_loss,
                          train_disc_loss, train_pix_embedding, train_prediction,
                          train_images, train_binary_labels, train_instance_labels, train_l2_reg_loss])

            cost_time = time.time() - t_start
            train_cost_time_mean.append(cost_time)
            # ============================== 透心凉，心飞扬
            if math.isnan(train_loss) or math.isnan(train_binary_loss) or math.isnan(train_instance_loss):
                log.error('cost is: {:.5f}'.format(train_loss))
                log.error('binary cost is: {:.5f}'.format(train_binary_loss))
                log.error('instance cost is: {:.5f}'.format(train_instance_loss))
                return
            # ==============================
            summary_writer.add_summary(summary=train_summary, global_step=step)

            # 每隔 DISPLAY_STEP 次，打印 loss 值
            if step % CFG.TRAIN.DISPLAY_STEP == 0:
                epoch_num = step // one_epoch2step
                log.info('Epoch: {:d} Step: {:d} total_loss= {:6f} binary_seg_loss= {:6f} '
                         'instance_seg_loss= {:6f} l2_reg_loss= {:6f} accuracy= {:6f} fp= {:6f} fn= {:6f}'
                         ' lr= {:6f} mean_cost_time= {:5f}s '.
                         format(epoch_num + 1, step + 1, train_loss, train_binary_loss, train_instance_loss,
                                train_l2_loss, train_accuracy_figure, train_fp_figure, train_fn_figure, lr,
                                np.mean(train_cost_time_mean)))
                train_cost_time_mean.clear()
            # # 每隔 VAL_DISPLAY_STEP 次，保存模型,保存当前 batch 训练结果图片
            # if step % CFG.TRAIN.VAL_DISPLAY_STEP == 0:
            #     saver.save(sess=sess, save_path=model_save_path, global_step=global_step) # global_step 会保存 global_step 信息
            #     record_training_intermediate_result(
            #         gt_images=train_gt_imgs, gt_binary_labels=train_binary_gt_labels,
            #         gt_instance_labels=train_instance_gt_labels, binary_seg_images=train_binary_seg_imgs,
            #         pix_embeddings=train_embeddings
            #     )
            # ---------------------------------------------------------------- #

            # ================================================================ #
            #                                Val                               #
            # ================================================================ #
            # 每隔 VAL_DISPLAY_STEP 次，测试整个验证集
            if step % CFG.TRAIN.VAL_DISPLAY_STEP == 0:
                val_t_start = time.time()
                val_cost_time = 0
                mean_val_c = 0.0
                mean_val_binary_loss = 0.0
                mean_val_instance_loss = 0.0
                mean_val_accuracy_figure = 0.0
                mean_val_fp_figure = 0.0
                mean_val_fn_figure = 0.0
                for val_step in range(val_steps):
                    # validation part
                    val_c, val_accuracy_figure, val_fn_figure, val_fp_figure, \
                        val_summary, val_binary_loss, val_instance_loss, \
                        val_embeddings, val_binary_seg_imgs, val_gt_imgs, \
                        val_binary_gt_labels, val_instance_gt_labels = \
                        sess.run([val_total_loss, val_accuracy, val_fn, val_fp,
                                  val_merge_summary_op, val_binary_seg_loss,
                                  val_disc_loss, val_pix_embedding, val_prediction,
                                  val_images, val_binary_labels, val_instance_labels])

                    # ============================== 透心凉，心飞扬
                    if math.isnan(val_c) or math.isnan(val_binary_loss) or math.isnan(val_instance_loss):
                        log.error('cost is: {:.5f}'.format(val_c))
                        log.error('binary cost is: {:.5f}'.format(val_binary_loss))
                        log.error('instance cost is: {:.5f}'.format(val_instance_loss))
                        return
                    # ==============================

                    # if val_step == 0:
                    #     record_training_intermediate_result(
                    #         gt_images=val_gt_imgs, gt_binary_labels=val_binary_gt_labels,
                    #         gt_instance_labels=val_instance_gt_labels, binary_seg_images=val_binary_seg_imgs,
                    #         pix_embeddings=val_embeddings, flag='val'
                    #     )

                    cost_time = time.time() - val_t_start
                    val_cost_time += cost_time
                    mean_val_c += val_c
                    mean_val_binary_loss += val_binary_loss
                    mean_val_instance_loss += val_instance_loss
                    mean_val_accuracy_figure += val_accuracy_figure
                    mean_val_fp_figure += val_fp_figure
                    mean_val_fn_figure += val_fn_figure
                    summary_writer.add_summary(summary=val_summary, global_step=step)

                mean_val_c /= val_steps
                mean_val_binary_loss /= val_steps
                mean_val_instance_loss /= val_steps
                mean_val_accuracy_figure /= val_steps
                mean_val_fp_figure /= val_steps
                mean_val_fn_figure /= val_steps

                # ==============================
                if mean_val_accuracy_figure > max_acc:
                    max_acc = mean_val_accuracy_figure
                    if save_num < 3: # 前三次不算
                        max_acc = 0.9
                    log.info('MAX_ACC change to {}'.format(mean_val_accuracy_figure))
                    model_save_path_max = ops.join(model_save_dir,
                                                   'tusimple_lanenet_{}.ckpt'.format(mean_val_accuracy_figure))
                    saver.save(sess=sess, save_path=model_save_path_max, global_step=global_step)
                    save_num += 1
                # ==============================

                log.info('MEAN Val: total_loss= {:6f} binary_seg_loss= {:6f} '
                         'instance_seg_loss= {:6f} accuracy= {:6f} fp= {:6f} fn= {:6f}'
                         ' mean_cost_time= {:5f}s '.
                         format(mean_val_c, mean_val_binary_loss, mean_val_instance_loss, mean_val_accuracy_figure,
                                mean_val_fp_figure, mean_val_fn_figure, val_cost_time))

            # ---------------------------------------------------------------- #
    return


def train_lanenet_multi_gpu(dataset_dir, weights_path=None, net_flag='vgg', version_flag='', scratch=False):
    """
    train lanenet with multi gpu
    :param dataset_dir:
    :param weights_path:
    :param net_flag:
    :return:
    """
    # set lanenet dataset
    train_dataset = lanenet_data_feed_pipline.LaneNetDataFeeder(
        dataset_dir=dataset_dir, flags='train'
    )
    val_dataset = lanenet_data_feed_pipline.LaneNetDataFeeder(
        dataset_dir=dataset_dir, flags='val'
    )

    # set lanenet
    train_net = lanenet.LaneNet(net_flag=net_flag, phase='train', reuse=False)
    val_net = lanenet.LaneNet(net_flag=net_flag, phase='val', reuse=True)

    # set compute graph node
    train_images, train_binary_labels, train_instance_labels = train_dataset.inputs(
        CFG.TRAIN.BATCH_SIZE, 1
    )
    val_images, val_binary_labels, val_instance_labels = val_dataset.inputs(
        CFG.TEST.BATCH_SIZE, 1
    )

    # set average container
    tower_grads = []
    train_tower_loss = []
    val_tower_loss = []
    batchnorm_updates = None
    train_summary_op_updates = None

    # set lr
    global_step = tf.Variable(0, trainable=False)
    learning_rate = tf.train.polynomial_decay(
        learning_rate=CFG.TRAIN.LEARNING_RATE,
        global_step=global_step,
        decay_steps=CFG.TRAIN.EPOCHS,
        power=0.9
    )

    # set optimizer
    optimizer = tf.train.MomentumOptimizer(
        learning_rate=learning_rate, momentum=CFG.TRAIN.MOMENTUM
    )

    # set distributed train op
    with tf.variable_scope(tf.get_variable_scope()):
        gpu_ids = GPU_IDS.split('')
        for idx, gpu_idx in enumerate(gpu_ids):
            with tf.device('/gpu:{:d}'.format(gpu_idx)):
                with tf.name_scope('tower_{:d}'.format(gpu_idx)) as _:
                    train_loss, grads = compute_net_gradients(
                        train_images, train_binary_labels, train_instance_labels, train_net, optimizer
                    )

                    # Only use the mean and var in the first gpu tower to update the parameter
                    if idx == 0:
                        batchnorm_updates = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
                        train_summary_op_updates = tf.get_collection(tf.GraphKeys.SUMMARIES)
                    tower_grads.append(grads)
                    train_tower_loss.append(train_loss)

                with tf.name_scope('validation_{:d}'.format(gpu_idx)) as _:
                    val_loss, _ = compute_net_gradients(
                        val_images, val_binary_labels, val_instance_labels, val_net, optimizer)
                    val_tower_loss.append(val_loss)

    grads = average_gradients(tower_grads)
    avg_train_loss = tf.reduce_mean(train_tower_loss)
    avg_val_loss = tf.reduce_mean(val_tower_loss)

    # Track the moving averages of all trainable variables
    variable_averages = tf.train.ExponentialMovingAverage(
        CFG.TRAIN.MOVING_AVERAGE_DECAY, num_updates=global_step)
    variables_to_average = tf.trainable_variables() + tf.moving_average_variables()
    variables_averages_op = variable_averages.apply(variables_to_average)

    # Group all the op needed for training
    batchnorm_updates_op = tf.group(*batchnorm_updates)
    apply_gradient_op = optimizer.apply_gradients(grads, global_step=global_step)
    train_op = tf.group(apply_gradient_op, variables_averages_op,
                        batchnorm_updates_op)

    # Set tf summary save path
    tboard_save_path = 'tboard/tusimple_lanenet_multi_gpu_{:s}'.format(net_flag)
    os.makedirs(tboard_save_path, exist_ok=True)

    summary_writer = tf.summary.FileWriter(tboard_save_path)

    avg_train_loss_scalar = tf.summary.scalar(
        name='average_train_loss', tensor=avg_train_loss
    )
    avg_val_loss_scalar = tf.summary.scalar(
        name='average_val_loss', tensor=avg_val_loss
    )
    learning_rate_scalar = tf.summary.scalar(
        name='learning_rate_scalar', tensor=learning_rate
    )

    train_merge_summary_op = tf.summary.merge(
        [avg_train_loss_scalar, learning_rate_scalar] + train_summary_op_updates
    )
    val_merge_summary_op = tf.summary.merge([avg_val_loss_scalar])

    # set tensorflow saver
    saver = tf.train.Saver()
    model_save_dir = 'model/tusimple_lanenet_multi_gpu_{:s}'.format(net_flag)
    os.makedirs(model_save_dir, exist_ok=True)
    train_start_time = time.strftime('%Y-%m-%d-%H-%M-%S', time.localtime(time.time()))
    model_name = 'tusimple_lanenet_{:s}_{:s}.ckpt'.format(net_flag, str(train_start_time))
    model_save_path = ops.join(model_save_dir, model_name)

    # set sess config
    sess_config = tf.ConfigProto(device_count={'GPU': len(CFG.TRAIN.GPU_IDS)}, allow_soft_placement=True)
    sess_config.gpu_options.per_process_gpu_memory_fraction = CFG.TRAIN.GPU_MEMORY_FRACTION
    sess_config.gpu_options.allow_growth = CFG.TRAIN.TF_ALLOW_GROWTH
    sess_config.gpu_options.allocator_type = 'BFC'

    # Set the training parameters
    train_epochs = CFG.TRAIN.EPOCHS

    log.info('Global configuration is as follows:')
    log.info(CFG)

    sess = tf.Session(config=sess_config)

    summary_writer.add_graph(sess.graph)

    with sess.as_default():

        tf.train.write_graph(
            graph_or_graph_def=sess.graph, logdir='',
            name='{:s}/lanenet_model.pb'.format(model_save_dir))

        if weights_path is None:
            log.info('Training from scratch')
            init = tf.global_variables_initializer()
            sess.run(init)
        else:
            log.info('Restore model from last model checkpoint {:s}'.format(weights_path))
            saver.restore(sess=sess, save_path=weights_path)

        train_cost_time_mean = []
        val_cost_time_mean = []

        for epoch in range(train_epochs):

            # training part
            t_start = time.time()

            _, train_loss_value, train_summary, lr = \
                sess.run(
                    fetches=[train_op, avg_train_loss,
                             train_merge_summary_op, learning_rate]
                )

            if math.isnan(train_loss_value):
                log.error('Train loss is nan')
                return

            cost_time = time.time() - t_start
            train_cost_time_mean.append(cost_time)

            summary_writer.add_summary(summary=train_summary, global_step=epoch)

            # validation part
            t_start_val = time.time()

            val_loss_value, val_summary = \
                sess.run(fetches=[avg_val_loss, val_merge_summary_op])

            summary_writer.add_summary(val_summary, global_step=epoch)

            cost_time_val = time.time() - t_start_val
            val_cost_time_mean.append(cost_time_val)

            if epoch % CFG.TRAIN.DISPLAY_STEP == 0:
                log.info('Epoch_Train: {:d} total_loss= {:6f} '
                         'lr= {:6f} mean_cost_time= {:5f}s '.
                         format(epoch + 1,
                                train_loss_value,
                                lr,
                                np.mean(train_cost_time_mean))
                         )
                train_cost_time_mean.clear()

            if epoch % CFG.TRAIN.VAL_DISPLAY_STEP == 0:
                log.info('Epoch_Val: {:d} total_loss= {:6f}'
                         ' mean_cost_time= {:5f}s '.
                         format(epoch + 1,
                                val_loss_value,
                                np.mean(val_cost_time_mean))
                         )
                val_cost_time_mean.clear()

            if epoch % 2000 == 0:
                saver.save(sess=sess, save_path=model_save_path, global_step=epoch)
    return


if __name__ == '__main__':
    # init args
    args = init_args()

    if len(GPU_IDS.split(',')) < 2:
        args.multi_gpus = False
    print('GPU_IDS: ', GPU_IDS)

    # train lanenet
    if not args.multi_gpus:
        train_lanenet(args.dataset_dir, args.weights_path, net_flag=args.net_flag,
                      version_flag=args.version_flag, scratch=args.scratch)
    else:
        train_lanenet_multi_gpu(args.dataset_dir, args.weights_path, net_flag=args.net_flag,
                                version_flag=args.version_flag, scratch=args.scratch)
"""
VGG:MEAN Val: total_loss= 2.950904 binary_seg_loss= 0.252387 instance_seg_loss= 0.774763 
              l2_reg_loss=1.923754 accuracy= 0.949175 fp= 0.855856 fn= 0.050825 
              mean_cost_time= 31198.629075s 
mobileV2（缺少res3_2) MEAN Val: total_loss= 9.678829 binary_seg_loss= 0.207078 instance_seg_loss= 0.873754 
              accuracy= 0.900191 fp= 0.835228 fn= 0.099809 mean_cost_time= 17556.474357s 
mobileV2 MEAN Val: total_loss= 8.534331 binary_seg_loss= 0.194427 instance_seg_loss= 0.735091 
              accuracy= 0.920086 fp= 0.834390 fn= 0.079914 mean_cost_time= 16953.547680s  
mobileV2 修改反卷积 MEAN Val: total_loss= 7.231174 binary_seg_loss= 0.226127 instance_seg_loss= 0.873370 
              accuracy= 0.929177 fp= 0.863174 fn= 0.070823 mean_cost_time= 15651.155647s 
mobileV2 上采样 MEAN Val: total_loss= 4.993308 binary_seg_loss= 0.257211 instance_seg_loss= 0.987948 
              accuracy= 0.886431 fp= 0.880646 fn= 0.113569 mean_cost_time= 7826.575856s 
"""