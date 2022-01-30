import lmdb
import six
import random
import time
import numpy as np
from PIL import Image, ImageFile
import cv2

from data_provider.generator_enqueuer import GeneratorEnqueuer
from data_provider.data_utils import get_vocabulary, rotate_img

def generator(lmdb_dir, input_height, input_width, batch_size, max_len, voc_type, keep_ratio=False, with_aug=False):
    env = lmdb.open(lmdb_dir, max_readers=32, readonly=True)
    txn = env.begin()

    num_samples = int(txn.get(b"num-samples").decode())
    print("There are {} images in {}".format(num_samples, lmdb_dir))
    index = np.arange(0, num_samples) # TODO check index is reliable

    voc, char2id, id2char = get_vocabulary(voc_type)
    is_lowercase = (voc_type == 'LOWERCASE')

    batch_images = []
    batch_labels = []
    batch_lengths = []
    batch_masks = []
    batch_labels_str = []

    while True:
        np.random.shuffle(index)
        for i in index:
            i += 1
            try:
                image_key = b'image-%09d' % i
                label_key = b'label-%09d' % i

                imgbuf = txn.get(image_key)
                buf = six.BytesIO()
                buf.write(imgbuf)
                buf.seek(0)

                img_pil = Image.open(buf).convert('RGB')
                img = np.array(img_pil)
                word = txn.get(label_key).decode()
                if is_lowercase:
                    word = word.lower()

                H, W, C = img.shape

                # Rotate the vertical images
                if H > 1.1 * W:
                    img = np.rot90(img)
                    H, W = W, H

                # Resize the images
                img_resize = np.zeros((input_height, input_width, C), dtype=np.uint8)

                # Data augmentation
                if with_aug:
                    ratn = random.randint(0, 3)
                    if ratn == 0:
                        rand_reg = random.random() * 90 - 45
                        img = rotate_img(img, rand_reg)

                if keep_ratio:
                    new_width = int((1.0 * input_height / H) * W)
                    new_width = new_width if new_width < width else width
                    new_height = input_height
                    img = cv2.resize(img, (new_width, new_height))
                    img_resize[:new_height, :new_width, :] = img.copy()
                else:
                    img_resize = cv2.resize(img, (input_width, input_height))
                img_resize = np.expand_dims(cv2.cvtColor(img_resize, cv2.COLOR_RGB2GRAY), axis=-1)

                if with_aug:
                    color = random.randint(0, 3)
                    if color == 0:
                        img_resize = 255 - img_resize

                label = np.full((max_len), char2id['EOS'], dtype=np.int)
                label_mask = np.full((max_len), 0, dtype=np.int)
                label_list = []
                for char in word:
                    if char in char2id:
                        label_list.append(char2id[char])
                    else:
                        continue
                if len(label_list) > (max_len - 1):
                    label_list = label_list[:(max_len - 1)]
                label[:len(label_list)] = np.array(label_list)

                if label.shape[0] <= 0:
                    continue

                label_len = len(label_list) + 1
                label_mask[:label_len] = 1

                batch_images.append(img_resize)
                batch_labels.append(label)
                batch_masks.append(label_mask)
                batch_lengths.append(label_len)
                batch_labels_str.append(word)

                assert len(batch_images) == len(batch_labels) == len(batch_lengths)

                if len(batch_images) == batch_size:
                    yield np.array(batch_images), np.array(batch_labels), np.array(batch_masks), np.array(batch_lengths), batch_labels_str
                    batch_images = []
                    batch_labels = []
                    batch_masks = []
                    batch_lengths = []
                    batch_labels_str = []

            except Exception as e:
                print(e)
                print("Error in %d" % i)
                continue

def get_batch(num_workers, **kwargs):
    try:
        enqueuer = GeneratorEnqueuer(generator(**kwargs), use_multiprocessing=True)
        print('Generator use 10 batches for buffering, this may take a while, you can tune this yourself.')
        enqueuer.start(max_queue_size=4, workers=num_workers)
        generator_output = None
        while True:
            while enqueuer.is_running():
                if not enqueuer.queue.empty():
                    generator_output = enqueuer.queue.get()
                    break
                else:
                    time.sleep(0.01)
            yield generator_output
            generator_output = None
    finally:
        if enqueuer is not None:
            enqueuer.stop()