from data import tfrecord_decoder
from utils import augmentation
from utils.utils import normalize_image
import tensorflow as tf
import matplotlib.pyplot as plt


class Parser(object):

    def __init__(self,
                 output_size,
                 anchor_instance,
                 match_threshold=0.6,
                 unmatched_threshold=0.5,
                 num_max_fix_padding=100,
                 proto_output_size=138,
                 skip_crow_during_training=True,
                 use_bfloat16=True,
                 mode=None):

        self._mode = mode
        self._skip_crowd_during_training = skip_crow_during_training
        self._is_training = (mode == "train")

        self._example_decoder = tfrecord_decoder.TfExampleDecoder()

        self._output_size = output_size
        self._anchor_instance = anchor_instance
        self._match_threshold = match_threshold
        self._unmatched_threshold = unmatched_threshold

        # output related
        # for classes and mask to be padded to fix length
        self._num_max_fix_padding = num_max_fix_padding
        # resize the mask to proto output size in advance (always 138, from paper's figure)
        self._proto_output_size = proto_output_size

        # Device.
        self._use_bfloat16 = use_bfloat16

        # Data is parsed depending on the model.
        if mode == "train":
            self._parse_fn = self._parse_train_data
        elif mode == "eval":
            self._parse_fn = self._parse_eval_data
        elif mode == "test":
            self._parse_fn = self._parse_predict_data
        else:
            raise ValueError('mode is not defined.')

    def __call__(self, value):
        with tf.name_scope('parser'):
            data = self._example_decoder.decode(value)
            return self._parse_fn(data)

    def _parse_train_data(self, data):
        is_crowds = data['gt_is_crowd']
        classes = data['gt_classes']
        boxes = data['gt_bboxes']
        masks = data['gt_masks']
        image_height = data['height']
        image_width = data['width']

        # Skips annotations with `is_crowd` = True.
        # Todo: Need to understand control_dependeicies and tf.gather
        tf.print("Ignore crowd annotation")
        if self._skip_crowd_during_training and self._is_training:
            num_groundtrtuhs = tf.shape(input=classes)[0]
            with tf.control_dependencies([num_groundtrtuhs, is_crowds]):
                indices = tf.cond(
                    pred=tf.greater(tf.size(input=is_crowds), 0),
                    true_fn=lambda: tf.where(tf.logical_not(is_crowds))[:, 0],
                    false_fn=lambda: tf.cast(tf.range(num_groundtrtuhs), tf.int64))
            classes = tf.gather(classes, indices)
            boxes = tf.gather(boxes, indices)
            masks = tf.gather(masks, indices)

        tf.print("num_gt = ", tf.shape(classes)[0])
        image = data['image']
        tf.print("box", boxes)
        tf.print("anchor", self._anchor_instance.get_anchors())

        # resize the image, box
        tf.print("Resize image")
        image = tf.image.resize(image, [self._output_size, self._output_size])

        # resize mask
        masks = tf.expand_dims(masks, axis=-1)
        masks = tf.image.resize(masks, [self._proto_output_size, self._proto_output_size])
        masks = tf.squeeze(masks)

        # resize boxes
        scale_x = tf.cast(self._output_size / image_width, tf.float32)
        scale_y = tf.cast(self._output_size / image_height, tf.float32)
        scales = tf.stack([scale_x, scale_y, scale_x, scale_y])
        boxes = boxes / scales
        # normalize the image
        tf.print("normalize image")
        image = normalize_image(image)

        # Todo: SSD data augmentation (Photometrics, expand, sample_crop, mirroring)
        # data augmentation randomly
        print("data augmentation")
        image, boxes, masks = augmentation.random_augmentation(image, boxes, masks)

        # Padding classes and mask to fix length [None, num_max_fix_padding, ...]
        num_padding = self._num_max_fix_padding - tf.shape(classes)[0]
        pad_classes = tf.zeros([num_padding], dtype=tf.int64)
        pad_masks = tf.zeros([num_padding, self._proto_output_size, self._proto_output_size])

        if tf.shape(classes)[0] == 1:
            masks = tf.expand_dims(masks, axis=0)
        masks = tf.concat([masks, pad_masks], axis=0)
        classes = tf.concat([classes, pad_classes], axis=0)

        tf.print("class", tf.shape(classes))
        tf.print("mask", tf.shape(masks))

        # match anchors
        print("anchor matching")
        cls_targets, box_targets, num_pos, max_id_for_anchors, match_positiveness = self._anchor_instance.matching(
            self._match_threshold, self._unmatched_threshold, boxes, classes)

        labels = {
            'cls_targets': cls_targets,
            'box_targets': box_targets,
            'num_positive': num_pos,
            'positiveness': match_positiveness,
            'classes': classes,
            'mask_target': masks
        }
        return image, labels

    def _parse_eval_data(self, data):
        pass

    def _parse_predict_data(self, data):
        pass