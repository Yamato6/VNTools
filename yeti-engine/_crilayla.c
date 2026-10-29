/* CRILAYLA en C (opcional, ~100x más rápido que la versión Python).
   out debe tener usize + 0x100 bytes. Devuelve 0 si todo fue bien. */
#include <stdint.h>
#include <string.h>

int crilayla_decompress(const uint8_t *in, int in_len, uint8_t *out) {
    uint32_t usize, hdr;
    memcpy(&usize, in + 8, 4);
    memcpy(&hdr, in + 12, 4);
    if ((long)hdr + 0x10 + 0x100 > in_len) return 1;
    memcpy(out, in + hdr + 0x10, 0x100);

    int pos = in_len - 0x100 - 1, left = 0;
    uint8_t pool = 0;
    long end = 0x100 + (long)usize - 1, written = 0;
    static const int lens[4] = {2, 3, 5, 8};

#define BITS(n, v) do { int need = (n), got = 0; v = 0; \
        while (got < need) { if (left == 0) { if (pos < 0) return 2; pool = in[pos--]; left = 8; } \
            int take = left < need - got ? left : need - got; \
            v = (v << take) | ((pool >> (left - take)) & ((1 << take) - 1)); \
            left -= take; got += take; } } while (0)

    while (written < (long)usize) {
        int flag; BITS(1, flag);
        if (flag) {
            int d; BITS(13, d);
            long ref = end - written + d + 3;
            long length = 3;
            int lv = 0, k;
            for (k = 0; k < 4; k++) {
                BITS(lens[k], lv);
                length += lv;
                if (lv != (1 << lens[k]) - 1) break;
            }
            if (k == 4) {
                do { BITS(8, lv); length += lv; } while (lv == 255);
            }
            for (long i = 0; i < length; i++) {
                if (ref < 0 || end - written < 0) return 3;
                out[end - written] = out[ref--];
                written++;
            }
        } else {
            int v; BITS(8, v);
            out[end - written] = (uint8_t)v;
            written++;
        }
    }
    return 0;
}
