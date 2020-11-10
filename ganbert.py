# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# Copyright Tor Vergata, University of Rome. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Here is defined the GAN-BERT model, starting from the run_classifier.py https://github.com/google-research/bert/blob/master/run_classifier.py
#

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import collections
import csv
import os
import modeling
import optimization
import tokenization
import tensorflow as tf
import numpy as np
import random
import math
import tf_metrics

from data_processors import InputFeatures, PaddingInputExample, QcFineProcessor


flags = tf.flags

FLAGS = flags.FLAGS

flags.DEFINE_integer(
    "unlabeled_multiplier", 100,
    "The multiplier to compute the max number of unlabeled examples with respect to the labeled examples.")

flags.DEFINE_string(
    "data_dir", None,
    "The input data dir. Should contain the .tsv files (or other data files) "
    "for the task.")

flags.DEFINE_string(
    "bert_config_file", None,
    "The config json file corresponding to the pre-trained BERT model. "
    "This specifies the model architecture.")

flags.DEFINE_string("task_name", None, "The name of the task to train.")

flags.DEFINE_string("vocab_file", None,
                    "The vocabulary file that the BERT model was trained on.")

flags.DEFINE_string(
    "output_dir", None,
    "The output directory where the model checkpoints will be written.")

flags.DEFINE_string(
    "init_checkpoint", None,
    "Initial checkpoint (usually from a pre-trained BERT model).")

flags.DEFINE_bool(
    "do_lower_case", True,
    "Whether to lower case the input text. Should be True for uncased "
    "models and False for cased models.")

flags.DEFINE_integer(
    "max_seq_length", 500,##128,
    "The maximum total input sequence length after WordPiece tokenization. "
    "Sequences longer than this will be truncated, and sequences shorter "
    "than this will be padded.")

flags.DEFINE_bool("do_train", False, "Whether to run training.")

flags.DEFINE_bool("do_eval", False, "Whether to run eval on the dev set.")

flags.DEFINE_bool(
    "do_predict", False,
    "Whether to run the model in inference mode on the test set.")

flags.DEFINE_integer("train_batch_size", 8, "Total batch size for training.")

flags.DEFINE_integer("eval_batch_size", 8, "Total batch size for eval.")

flags.DEFINE_integer("predict_batch_size", 8, "Total batch size for predict.")

flags.DEFINE_float("learning_rate", 5e-5, "The initial learning rate for Adam.")

flags.DEFINE_float("num_train_epochs", 3.0,
                   "Total number of training epochs to perform.")

flags.DEFINE_float(
    "warmup_proportion", 0.1,
    "Proportion of training to perform linear learning rate warmup for. "
    "E.g., 0.1 = 10% of training.")

flags.DEFINE_integer("save_checkpoints_steps", 1000,
                     "How often to save the model checkpoint.")

flags.DEFINE_integer("iterations_per_loop", 1000,
                     "How many steps to make in each estimator call.")

flags.DEFINE_bool("use_tpu", False, "Whether to use TPU or GPU/CPU.")

tf.flags.DEFINE_string(
    "tpu_name", None,
    "The Cloud TPU to use for training. This should be either the name "
    "used when creating the Cloud TPU, or a grpc://ip.address.of.tpu:8470 "
    "url.")

tf.flags.DEFINE_string(
    "tpu_zone", None,
    "[Optional] GCE zone where the Cloud TPU is located in. If not "
    "specified, we will attempt to automatically detect the GCE project from "
    "metadata.")

tf.flags.DEFINE_string(
    "gcp_project", None,
    "[Optional] Project name for the Cloud TPU-enabled project. If not "
    "specified, we will attempt to automatically detect the GCE project from "
    "metadata.")

tf.flags.DEFINE_string("master", None, "[Optional] TensorFlow master URL.")

flags.DEFINE_integer(
    "num_tpu_cores", 8,
    "Only used if `use_tpu` is True. Total number of TPU cores to use.")

flags.DEFINE_float("label_rate", 1.0,
                   "Rate for labeled examples (Used only for logging purpose).")

flags.DEFINE_float("dropout_keep_rate", 0.9,
                   "Keep rate for dropout.")

epsilon = 1e-8
DKP = FLAGS.dropout_keep_rate
LATENT_Z = 100

SEED = 0
np.random.seed(SEED)
tf.compat.v1.set_random_seed(SEED)
random.seed(SEED)


def convert_single_example(ex_index, example, label_list, max_seq_length,
                           tokenizer, label_mask):
  """Converts a single `InputExample` into a single `InputFeatures`."""

  if isinstance(example, PaddingInputExample):
    return InputFeatures(
        input_ids=[0] * max_seq_length,
        input_mask=[0] * max_seq_length,
        segment_ids=[0] * max_seq_length,
        label_id=0,
        label_mask=label_mask,
        is_real_example=False)

  label_map = {}
  for (i, label) in enumerate(label_list):
    label_map[label] = i

  tokens_a = tokenizer.tokenize(example.text_a)
  tokens_b = None
  if example.text_b:
    tokens_b = tokenizer.tokenize(example.text_b)

  if tokens_b:
    # Modifies `tokens_a` and `tokens_b` in place so that the total
    # length is less than the specified length.
    # Account for [CLS], [SEP], [SEP] with "- 3"
    _truncate_seq_pair(tokens_a, tokens_b, max_seq_length - 3)
  else:
    # Account for [CLS] and [SEP] with "- 2"
    if len(tokens_a) > max_seq_length - 2:
      tokens_a = tokens_a[0:(max_seq_length - 2)]

  # The convention in BERT is:
  # (a) For sequence pairs:
  #  tokens:   [CLS] is this jack ##son ##ville ? [SEP] no it is not . [SEP]
  #  type_ids: 0     0  0    0    0     0       0 0     1  1  1  1   1 1
  # (b) For single sequences:
  #  tokens:   [CLS] the dog is hairy . [SEP]
  #  type_ids: 0     0   0   0  0     0 0
  #
  # Where "type_ids" are used to indicate whether this is the first
  # sequence or the second sequence. The embedding vectors for `type=0` and
  # `type=1` were learned during pre-training and are added to the wordpiece
  # embedding vector (and position vector). This is not *strictly* necessary
  # since the [SEP] token unambiguously separates the sequences, but it makes
  # it easier for the model to learn the concept of sequences.
  #
  # For classification tasks, the first vector (corresponding to [CLS]) is
  # used as the "sentence vector". Note that this only makes sense because
  # the entire model is fine-tuned.
  tokens = []
  segment_ids = []
  tokens.append("[CLS]")
  segment_ids.append(0)
  for token in tokens_a:
    tokens.append(token)
    segment_ids.append(0)
  tokens.append("[SEP]")
  segment_ids.append(0)

  if tokens_b:
    for token in tokens_b:
      tokens.append(token)
      segment_ids.append(1)
    tokens.append("[SEP]")
    segment_ids.append(1)

  input_ids = tokenizer.convert_tokens_to_ids(tokens)

  # The mask has 1 for real tokens and 0 for padding tokens. Only real
  # tokens are attended to.
  input_mask = [1] * len(input_ids)

  # Zero-pad up to the sequence length.
  while len(input_ids) < max_seq_length:
    input_ids.append(0)
    input_mask.append(0)
    segment_ids.append(0)

  assert len(input_ids) == max_seq_length
  assert len(input_mask) == max_seq_length
  assert len(segment_ids) == max_seq_length

  label_id = [label_map[t] for t in example.label]
  # if ex_index < 5:
  #   tf.logging.info("*** Example ***")
  #   tf.logging.info("guid: %s" % (example.guid))
  #   tf.logging.info("tokens: %s" % " ".join(
  #       [tokenization.printable_text(x) for x in tokens]))
  #   tf.logging.info("input_ids: %s" % " ".join([str(x) for x in input_ids]))
  #   tf.logging.info("input_mask: %s" % " ".join([str(x) for x in input_mask]))
  #   tf.logging.info("segment_ids: %s" % " ".join([str(x) for x in segment_ids]))
  #   tf.logging.info("label: %s (id = %d)" % (example.label, label_id))

  feature = InputFeatures(
      input_ids=input_ids,
      input_mask=input_mask,
      segment_ids=segment_ids,
      label_id=label_id,
      label_mask=label_mask,
      is_real_example=True)
  return feature


def file_based_convert_examples_to_features(
    labeled_examples, unlabeled_examples, label_list, max_seq_length, tokenizer, output_file, label_mask_rate, is_testing=False):
  """Convert a set of `InputExample`s to a TFRecord file."""
  all_examples = labeled_examples
  if unlabeled_examples:
    all_examples = all_examples + unlabeled_examples
  label_masks = get_labeled_mask(mask_size=len(all_examples), labeled_size=len(labeled_examples))

  to_write_examples = list()
  for ex_index, example in enumerate(all_examples):
    if ex_index % 10000 == 0:
      tf.logging.info("Writing example %d" % ex_index)
    feature = convert_single_example(ex_index, example, label_list,
                                     max_seq_length, tokenizer, label_masks[ex_index])




    def create_int_feature(values):
      f = tf.train.Feature(int64_list=tf.train.Int64List(value=list(values)))
      return f

    features = collections.OrderedDict()
    features["input_ids"] = create_int_feature(feature.input_ids)
    features["input_mask"] = create_int_feature(feature.input_mask)
    features["segment_ids"] = create_int_feature(feature.segment_ids)
    features["label_ids"] = create_int_feature(feature.label_id)
    features["label_mask"] = create_int_feature([feature.label_mask])
    features["is_real_example"] = create_int_feature(
        [int(feature.is_real_example)])

    tf_example = tf.train.Example(features=tf.train.Features(feature=features))

    if label_mask_rate == 1:
        to_write_examples.append(tf_example)
    else:
        # IT SIMULATE A LABELED EXAMPLE
        if feature.label_mask:
            balance = int(1/label_mask_rate)
            balance = int(math.log(balance,2))
            if balance < 1:
                balance = 1
            for b in range(0, int(balance)):
                to_write_examples.append(tf_example)
        else:
          to_write_examples.append(tf_example)

  writer = tf.python_io.TFRecordWriter(output_file)
  written_examples = 0
  if not is_testing:
    random.shuffle(to_write_examples)
  for tf_example in to_write_examples:
    writer.write(tf_example.SerializeToString())
    written_examples = written_examples + 1
  writer.close()

  return written_examples


def file_based_input_fn_builder(input_file, seq_length, is_training, drop_remainder):
  """Creates an `input_fn` closure to be passed to TPUEstimator."""

  name_to_features = {
      "input_ids": tf.FixedLenFeature([seq_length], tf.int64),
      "input_mask": tf.FixedLenFeature([seq_length], tf.int64),
      "segment_ids": tf.FixedLenFeature([seq_length], tf.int64),
      "label_ids": tf.FixedLenFeature([], tf.int64),
      "is_real_example": tf.FixedLenFeature([], tf.int64),
      "label_mask": tf.FixedLenFeature([], tf.int64),
  }

  def _decode_record(record, name_to_features):
    """Decodes a record to a TensorFlow example."""
    example = tf.parse_single_example(record, name_to_features)

    # tf.Example only supports tf.int64, but the TPU only supports tf.int32.
    # So cast all int64 to int32.
    for name in list(example.keys()):
      t = example[name]
      if t.dtype == tf.int64:
        t = tf.to_int32(t)
      example[name] = t

    return example

  def input_fn(params):
    """The actual input function."""
    if is_training:
        batch_size = FLAGS.train_batch_size
    else:
        batch_size = params["batch_size"]

    # For training, we want a lot of parallel reading and shuffling.
    # For eval, we want no shuffling and parallel reading doesn't matter.
    d = tf.data.TFRecordDataset(input_file)
    if is_training:
      d = d.repeat()
      d = d.shuffle(buffer_size=10000, seed=SEED)

    d = d.apply(
        tf.contrib.data.map_and_batch(
            lambda record: _decode_record(record, name_to_features),
            batch_size=batch_size,
            drop_remainder=drop_remainder))

    return d

  return input_fn


def _truncate_seq_pair(tokens_a, tokens_b, max_length):
  """Truncates a sequence pair in place to the maximum length."""

  # This is a simple heuristic which will always truncate the longer sequence
  # one token at a time. This makes more sense than truncating an equal percent
  # of tokens from each, since if one sequence is very short then each token
  # that's truncated likely contains more information than a longer sequence.
  while True:
    total_length = len(tokens_a) + len(tokens_b)
    if total_length <= max_length:
      break
    if len(tokens_a) > len(tokens_b):
      tokens_a.pop()
    else:
      tokens_b.pop()


############ Defining Discriminator ############
def discriminator(x, d_hidden_size, dkp, is_training, num_labels, num_hidden_discriminator = 1, reuse = False):
    with tf.compat.v1.variable_scope('Discriminator', reuse = reuse):
        layer_hidden = tf.nn.dropout(x, keep_prob=dkp)
        for i in range(num_hidden_discriminator):
            layer_hidden = tf.layers.dense(layer_hidden, d_hidden_size)
            layer_hidden = tf.nn.leaky_relu(layer_hidden)
            layer_hidden = tf.nn.dropout(layer_hidden, keep_prob=dkp)
        flatten5 = layer_hidden

        logit = tf.layers.dense(layer_hidden, (num_labels + 1))
        prob = tf.nn.softmax(logit)
    return flatten5, logit, prob


############ Defining Generator ############
def generator(z, g_hidden_size, dkp, is_training, num_hidden_generator = 1, reuse = False):
    with tf.compat.v1.variable_scope('Generator', reuse = reuse):
        layer_hidden = z

        for i in range(num_hidden_generator):
            layer_hidden = tf.layers.dense(layer_hidden, g_hidden_size)
            layer_hidden = tf.nn.leaky_relu(layer_hidden)
            layer_hidden = tf.nn.dropout(layer_hidden, rate = 1 - dkp)
        layer_hidden = tf.layers.dense(layer_hidden, g_hidden_size)

    return layer_hidden


def create_model(bert_config, is_training, input_ids, input_mask, segment_ids,
                 labels, num_labels, use_one_hot_embeddings, label_mask):
  """Creates a classification model."""
  model = modeling.BertModel(
      config=bert_config,
      is_training=is_training,
      input_ids=input_ids,
      input_mask=input_mask,
      token_type_ids=segment_ids,
      use_one_hot_embeddings=use_one_hot_embeddings)

  output_layer = model.get_pooled_output()

  sess = tf.Session()

  sess.run(tf.Print(input_ids))
  exit()

  hidden_size = output_layer.shape[-1].value

  keep_prob = 1
  if is_training:
      keep_prob = DKP

  D_real_features, D_real_logits, D_real_prob = discriminator(output_layer, hidden_size, keep_prob, is_training,
                                                              num_labels, reuse=False)

  logits = D_real_logits[:, 1:]
  probabilities = tf.nn.softmax(logits, axis=-1)

  log_probs = tf.nn.log_softmax(logits, axis=-1)



  one_hot_labels = tf.sparse_to_dense(labels, [labels.shape[0], num_labels], 1.0, 0.0)

  # one_hot_labels = tf.one_hot(labels, depth=num_labels, dtype=tf.float32)

  if is_training:
    per_example_loss = -tf.reduce_sum(one_hot_labels * log_probs, axis=-1)
    per_example_loss = tf.boolean_mask(per_example_loss, label_mask)

    labeled_example_count = tf.cast(tf.size(per_example_loss), tf.float32)
    D_L_Supervised = tf.divide(tf.reduce_sum(per_example_loss), tf.maximum(labeled_example_count, 1))
  else:
    per_example_loss = -tf.reduce_sum(one_hot_labels * log_probs, axis=-1)
    D_L_Supervised = tf.reduce_mean(per_example_loss)

  z = tf.random_uniform([FLAGS.train_batch_size, LATENT_Z], minval=0, maxval=1, dtype=tf.float32, seed=SEED, name=None)
  x_g = generator(z, hidden_size, keep_prob, is_training=is_training, reuse=False)
  D_fake_features, DU_fake_logits, DU_fake_prob = discriminator(x_g, hidden_size, keep_prob, is_training, num_labels, reuse=True)
  
  D_L_unsupervised1U = -1 * tf.reduce_mean(tf.math.log(1 - D_real_prob[:, 0] + epsilon))
  D_L_unsupervised2U = -1 * tf.reduce_mean(tf.math.log(DU_fake_prob[:, 0] + epsilon))
  d_loss =  D_L_Supervised + D_L_unsupervised1U + D_L_unsupervised2U
  
  g_loss = -1 * tf.reduce_mean(tf.math.log(1 - DU_fake_prob[:, 0] + epsilon))
  G_feat_match = tf.reduce_mean(tf.square(tf.reduce_mean(D_real_features, axis=0) - tf.reduce_mean(D_fake_features, axis=0)))
  g_loss = g_loss + G_feat_match

  return (d_loss, g_loss, per_example_loss, logits, probabilities)


def model_fn_builder(bert_config, num_labels, init_checkpoint, learning_rate,
                     num_train_steps, num_warmup_steps, use_tpu,
                     use_one_hot_embeddings):
  """Returns `model_fn` closure for TPUEstimator."""

  def model_fn(features, labels, mode, params):
    """The `model_fn` for TPUEstimator."""

    # tf.logging.info("*** Features ***")
    # for name in sorted(features.keys()):
    #   tf.logging.info("  name = %s, shape = %s" % (name, features[name].shape))
    input_ids = features["input_ids"]
    input_mask = features["input_mask"]
    segment_ids = features["segment_ids"]
    label_ids = features["label_ids"]
    label_mask = features["label_mask"]

    is_real_example = None
    if "is_real_example" in features:
      is_real_example = tf.cast(features["is_real_example"], dtype=tf.float32)
    else:
      is_real_example = tf.ones(tf.shape(label_ids), dtype=tf.float32)

    is_training = (mode == tf.estimator.ModeKeys.TRAIN)

    (d_loss, g_loss, per_example_loss, logits, probabilities) = create_model(
        bert_config, is_training, input_ids, input_mask, segment_ids, label_ids,
        num_labels, use_one_hot_embeddings, label_mask)

    tvars = tf.trainable_variables()

    bert_vars = [v for v in tvars if 'bert' in v.name]
    d_vars = bert_vars + [v for v in tvars if 'Discriminator' in v.name]
    g_vars = [v for v in tvars if 'Generator' in v.name]

    initialized_variable_names = {}
    scaffold_fn = None
    if init_checkpoint:
      (assignment_map, initialized_variable_names
      ) = modeling.get_assignment_map_from_checkpoint(tvars, init_checkpoint)
      if use_tpu:

        def tpu_scaffold():
          tf.train.init_from_checkpoint(init_checkpoint, assignment_map)
          return tf.train.Scaffold()

        scaffold_fn = tpu_scaffold
      else:
        tf.train.init_from_checkpoint(init_checkpoint, assignment_map)

    tf.logging.info("**** Trainable Variables ****")
    for var in tvars:
      init_string = ""
      if var.name in initialized_variable_names:
        init_string = ", *INIT_FROM_CKPT*"
      # tf.logging.info("  name = %s, shape = %s%s", var.name, var.shape,
      #                 init_string)

    output_spec = None
    if mode == tf.estimator.ModeKeys.TRAIN:

      d_train_op = optimization.create_optimizer("d", d_vars,
          d_loss, learning_rate, num_train_steps, num_warmup_steps, use_tpu)


      g_train_op = optimization.create_optimizer("g", g_vars,
          g_loss, learning_rate, num_train_steps, num_warmup_steps, use_tpu)

      logging_hook = tf.train.LoggingTensorHook({"d_loss": d_loss, "g_loss": g_loss, "per_example_loss": per_example_loss}, every_n_iter=1)

      output_spec = tf.contrib.tpu.TPUEstimatorSpec(
          mode=mode,
          loss=d_loss + g_loss,
          train_op=tf.group(d_train_op, g_train_op),
          training_hooks=[logging_hook],
          scaffold_fn=scaffold_fn)
    elif mode == tf.estimator.ModeKeys.EVAL:
      def metric_fn(per_example_loss, label_ids, logits, is_real_example):
        predictions = tf.argmax(logits, axis=-1, output_type=tf.int32)
        accuracy = tf.metrics.accuracy(
            labels=label_ids, predictions=predictions, weights=is_real_example)
        precision = tf_metrics.precision(labels=label_ids, predictions=predictions, num_classes=num_labels,
                                         weights=is_real_example)
        recall = tf_metrics.recall(labels=label_ids, predictions=predictions, num_classes=num_labels,
                                   weights=is_real_example)
        f1_micro = tf_metrics.f1(labels=label_ids, predictions=predictions, num_classes=num_labels,
                           weights=is_real_example, average='micro')
        f1_macro = tf_metrics.f1(labels=label_ids, predictions=predictions, num_classes=num_labels,
                                 weights=is_real_example, average='macro')
        loss = tf.metrics.mean(values=per_example_loss, weights=is_real_example)
        return {
            "eval_accuracy": accuracy,
            "eval_precision": precision,
            "eval_recall": recall,
            "eval_f1_micro": f1_micro,
            "eval_f1_macro": f1_macro,
            "eval_loss": loss,
        }

      eval_metrics = (metric_fn,
                      [per_example_loss, label_ids, logits, is_real_example])
      output_spec = tf.contrib.tpu.TPUEstimatorSpec(
          mode=mode,
          loss=d_loss,
          eval_metrics=eval_metrics,
          scaffold_fn=scaffold_fn)
    else:
      output_spec = tf.contrib.tpu.TPUEstimatorSpec(
          mode=mode,
          predictions={"probabilities": probabilities},
          scaffold_fn=scaffold_fn)
    return output_spec

  return model_fn


def get_labeled_mask(mask_size, labeled_size):
    labeled_mask = np.zeros([mask_size], dtype = np.int16)
    labeled_mask[range(labeled_size)] = 1
    labeled_mask = 0.5 < labeled_mask
    return labeled_mask


def evaluate(estimator, label_rate, eval_examples, task_name, label_list, tokenizer):
    num_actual_eval_examples = len(eval_examples)
    if FLAGS.use_tpu:
        # TPU requires a fixed batch size for all batches, therefore the number
        # of examples must be a multiple of the batch size, or else examples
        # will get dropped. So we pad with fake examples which are ignored
        # later on. These do NOT count towards the metric (all tf.metrics
        # support a per-instance weight, and these get a weight of 0.0).
        while len(eval_examples) % FLAGS.eval_batch_size != 0:
            eval_examples.append(PaddingInputExample())



    eval_file = os.path.join(FLAGS.output_dir, "eval_"+str(task_name)+".tf_record")
    file_based_convert_examples_to_features(
        eval_examples, None, label_list, FLAGS.max_seq_length, tokenizer, eval_file, label_mask_rate=1)



    tf.logging.info("***** Running evaluation *****")
    tf.logging.info("  Num examples = %d (%d actual, %d padding)",
                    len(eval_examples), num_actual_eval_examples,
                    len(eval_examples) - num_actual_eval_examples)
    tf.logging.info("  Batch size = %d", FLAGS.eval_batch_size)

    #  This tells the estimator to run through the entire set.
    eval_steps = None
    # However, if running eval on the TPU, you will need to specify the
    # number of steps.
    if FLAGS.use_tpu:
        assert len(eval_examples) % FLAGS.eval_batch_size == 0
        eval_steps = int(len(eval_examples) // FLAGS.eval_batch_size)

    eval_drop_remainder = True if FLAGS.use_tpu else False
    eval_input_fn = file_based_input_fn_builder(
        input_file=eval_file,
        seq_length=FLAGS.max_seq_length,
        is_training=False,
        drop_remainder=eval_drop_remainder)



    result = estimator.evaluate(input_fn=eval_input_fn, steps=eval_steps)

    overall_result_file = open(task_name + "_statistics_GANBERT" + str(label_rate) + ".txt", "a+")

    for key in sorted(result.keys()):
        overall_result_file.write(str(label_rate) + " ")
        overall_result_file.write("%s = %s " % (key, str(result[key])))
    overall_result_file.write("\n")

    output_eval_file = os.path.join(FLAGS.output_dir, "eval_results_"+str(task_name)+".txt")
    with tf.gfile.GFile(output_eval_file, "w") as writer:
        tf.logging.info("***** Eval results *****")
        for key in sorted(result.keys()):
            tf.logging.info("  %s = %s", key, str(result[key]))
            writer.write("%s = %s\n" % (key, str(result[key])))


def main(_):
  # tf.logging.set_verbosity(tf.logging.INFO)

  label_rate = FLAGS.label_rate

  processors = {"qc-fine": QcFineProcessor}

  tokenization.validate_case_matches_checkpoint(FLAGS.do_lower_case,
                                                FLAGS.init_checkpoint)

  if not FLAGS.do_train and not FLAGS.do_eval and not FLAGS.do_predict:
    raise ValueError(
        "At least one of `do_train`, `do_eval` or `do_predict' must be True.")

  bert_config = modeling.BertConfig.from_json_file(FLAGS.bert_config_file)

  if FLAGS.max_seq_length > bert_config.max_position_embeddings:
    raise ValueError(
        "Cannot use sequence length %d because the BERT model "
        "was only trained up to sequence length %d" %
        (FLAGS.max_seq_length, bert_config.max_position_embeddings))

  tf.gfile.MakeDirs(FLAGS.output_dir)

  task_name = FLAGS.task_name.lower()

  if task_name not in processors:
    raise ValueError("Task not found: %s" % (task_name))

  processor = processors[task_name]()
  processor._create_examples(input_file='../../datasets/multiLabel_text_classification/ProgrammerWeb/programweb-data.csv')

  label_list = processor.get_labels()
  print(label_list)
  print(len(label_list))

  tokenizer = tokenization.FullTokenizer(
      vocab_file=FLAGS.vocab_file, do_lower_case=FLAGS.do_lower_case)

  tpu_cluster_resolver = None
  if FLAGS.use_tpu and FLAGS.tpu_name:
    tpu_cluster_resolver = tf.contrib.cluster_resolver.TPUClusterResolver(
        FLAGS.tpu_name, zone=FLAGS.tpu_zone, project=FLAGS.gcp_project)

  is_per_host = tf.contrib.tpu.InputPipelineConfig.PER_HOST_V2

  run_config = tf.contrib.tpu.RunConfig(
    cluster=tpu_cluster_resolver,
    master=FLAGS.master,
    model_dir=FLAGS.output_dir,
    save_checkpoints_steps=FLAGS.save_checkpoints_steps,
    tpu_config=tf.contrib.tpu.TPUConfig(
        iterations_per_loop=FLAGS.iterations_per_loop,
        num_shards=FLAGS.num_tpu_cores,
        per_host_input_for_training=is_per_host))

  num_train_steps = None
  num_warmup_steps = None
  if FLAGS.do_train:
    labeled_examples = processor.get_labeled_examples(FLAGS.data_dir)
    unlabeled_examples = processor.get_unlabeled_examples(FLAGS.data_dir)

    num_train_examples = len(labeled_examples) + len(unlabeled_examples)
    print(num_train_examples)

    num_train_steps = int(
         num_train_examples / FLAGS.train_batch_size * FLAGS.num_train_epochs)
    num_warmup_steps = int(num_train_steps * FLAGS.warmup_proportion)

  model_fn = model_fn_builder(
      bert_config=bert_config,
      num_labels=len(label_list),
      init_checkpoint=FLAGS.init_checkpoint,
      learning_rate=FLAGS.learning_rate,
      num_train_steps=num_train_steps,
      num_warmup_steps=num_warmup_steps,
      use_tpu=FLAGS.use_tpu,
      use_one_hot_embeddings=FLAGS.use_tpu)

  # If TPU is not available, this will fall back to normal Estimator on CPU
  # or GPU.
  estimator = tf.contrib.tpu.TPUEstimator(
      use_tpu=FLAGS.use_tpu,
      model_fn=model_fn,
      config=run_config,
      train_batch_size=FLAGS.train_batch_size,
      eval_batch_size=FLAGS.eval_batch_size,
      predict_batch_size=FLAGS.predict_batch_size)

  if FLAGS.do_train:
    train_file = os.path.join(FLAGS.output_dir, "train.tf_record")
    num_written_examples = file_based_convert_examples_to_features(
        labeled_examples, unlabeled_examples, label_list, FLAGS.max_seq_length, tokenizer, train_file,
        label_mask_rate=label_rate)

    real_num_train_steps = int(
         num_written_examples / FLAGS.train_batch_size * FLAGS.num_train_epochs)

    tf.logging.info("***** Running training *****")
    tf.logging.info("  Num examples = %d", len(labeled_examples) + len(unlabeled_examples))
    tf.logging.info("  Batch size = %d", FLAGS.train_batch_size)
    tf.logging.info("  Num steps = %d", real_num_train_steps)
    train_input_fn = file_based_input_fn_builder(
        input_file=train_file,
        seq_length=FLAGS.max_seq_length,
        is_training=True,
        drop_remainder=True)



    estimator.train(input_fn=train_input_fn, max_steps=real_num_train_steps)

    print("hhh")
    exit()

  if FLAGS.do_eval:
    eval_examples = processor.get_test_examples(FLAGS.data_dir)

    evaluate(estimator=estimator, label_rate=label_rate, eval_examples=eval_examples,
              task_name=task_name, label_list=label_list, tokenizer=tokenizer)

  if FLAGS.do_predict:
    predict_examples = processor.get_test_examples(FLAGS.data_dir)
    num_actual_predict_examples = len(predict_examples)
    if FLAGS.use_tpu:
      # TPU requires a fixed batch size for all batches, therefore the number
      # of examples must be a multiple of the batch size, or else examples
      # will get dropped. So we pad with fake examples which are ignored
      # later on.
      while len(predict_examples) % FLAGS.predict_batch_size != 0:
        predict_examples.append(PaddingInputExample())

    predict_file = os.path.join(FLAGS.output_dir, "predict.tf_record")
    file_based_convert_examples_to_features(predict_examples, None, label_list,
                                            FLAGS.max_seq_length, tokenizer,
                                            predict_file, label_mask_rate=label_rate, is_testing=True)

    tf.logging.info("***** Running prediction*****")
    tf.logging.info("  Num examples = %d (%d actual, %d padding)",
                    len(predict_examples), num_actual_predict_examples,
                    len(predict_examples) - num_actual_predict_examples)
    tf.logging.info("  Batch size = %d", FLAGS.predict_batch_size)

    predict_drop_remainder = True if FLAGS.use_tpu else False
    predict_input_fn = file_based_input_fn_builder(
        input_file=predict_file,
        seq_length=FLAGS.max_seq_length,
        is_training=False,
        drop_remainder=predict_drop_remainder)

    result = estimator.predict(input_fn=predict_input_fn)


    output_predict_file = os.path.join(FLAGS.output_dir, "test_results.tsv")
    with tf.gfile.GFile(output_predict_file, "w") as writer:
      num_written_lines = 0
      tf.logging.info("***** Predict results *****")
      for (i, prediction) in enumerate(result):
        probabilities = prediction["probabilities"]
        if i >= num_actual_predict_examples:
          break
        output_line = "\t".join(
            str(class_probability)
            for class_probability in probabilities) + "\n"
        writer.write(output_line)
        num_written_lines += 1
    assert num_written_lines == num_actual_predict_examples


if __name__ == "__main__":
  flags.mark_flag_as_required("data_dir")
  flags.mark_flag_as_required("task_name")
  flags.mark_flag_as_required("vocab_file")
  flags.mark_flag_as_required("bert_config_file")
  flags.mark_flag_as_required("output_dir")
  tf.app.run()
