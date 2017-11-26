import re
import bitmath
import math

def size_to_bytes(human_size):
    PARSE_REGEXP = r"(\d+)([MGTPE]i)"
    parse = re.compile(PARSE_REGEXP)
    try:
        size, unit = re.match(parse, human_size).group(1, 2)
        size = int(size)
        assert size > 0

        if unit == 'Mi':
            return int(bitmath.MiB(size).to_Byte())
        elif unit == 'Gi':
            return int(bitmath.GiB(size).to_Byte())
        elif unit == 'Ti':
            return int(bitmath.TiB(size).to_Byte())
        elif unit == 'Pi':
            return int(bitmath.PiB(size).to_Byte())
        elif unit == 'Ei':
            return int(bitmath.EiB(size).to_Byte())
        else:
            return 0
    except Exception as e:
        return 0

def bytes_to_size(size):
    units = ("B", "Ki", "Mi", "Gi", "Ti", "Pi", "Ei") 
    if size == 0:
        return "%d%s" % (size, units[0])

    i = int(math.floor(math.log(size, 1024)))
    p = math.pow(1024, i)
    s = round(size / p, 2)
    return "%d%s" % (s, units[i])

