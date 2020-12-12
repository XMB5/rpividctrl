from gi.repository import Gst


def get_pad(pads_iterator):
    while True:
        iterator_result, item = pads_iterator.next()
        if isinstance(item, Gst.Pad):
            return item
        if iterator_result == Gst.IteratorResult.DONE:
            raise ValueError('could not find pad from iterator')


def dict_to_struct(fields, name='fields'):
    struct = Gst.Structure.new_empty(name)
    for key, val in fields.items():
        struct.set_value(key, val)
    return struct


STATS_BUFFER_LEN = 50  # average last n samples
