/*
 * _siglus.c - Bucles lentos de Siglus Tools: LZ de SiglusEngine / G00 y audio NWA
 *
 * Referencia: GARbro ArcFormats/RealLive (ImageG00.cs, AudioNWA.cs), MIT.
 *
 * Compilar:  gcc -O2 -shared -o _siglus.dll _siglus.c
 * Las funciones devuelven los bytes escritos (>= 0) o un número negativo si los datos están mal.
 */

#include <stdint.h>
#include <string.h>

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif

/*
 * LZ de Siglus: un byte de banderas (bit menos significativo primero); bit 1 = una unidad
 * literal de bpp bytes; bit 0 = u16: (v & 0xF) + min_count unidades desde (v >> 4) unidades atrás.
 * Scene.pck y Gameexe.dat usan bpp = 1 y min_count = 2; G00 tipo 0 usa bpp = 3 y min_count = 1.
 */
EXPORT int64_t lz_unpack(const uint8_t *src, int64_t src_len, uint8_t *dst, int64_t dst_len,
                         int min_count, int bpp)
{
    int64_t s = 0, d = 0;
    unsigned bits = 2;
    while (d < dst_len && s < src_len) {
        bits >>= 1;
        if (bits == 1) {
            bits = src[s++] | 0x100;
            if (s > src_len) break;
        }
        if (bits & 1) {
            if (s + bpp > src_len) break;
            int64_t n = bpp;
            if (d + n > dst_len) n = dst_len - d;
            memcpy(dst + d, src + s, n);
            s += bpp;
            d += n;
        } else {
            if (s + 2 > src_len) break;
            unsigned v = src[s] | (src[s + 1] << 8);
            s += 2;
            int64_t count = ((int64_t)(v & 0xF) + min_count) * bpp;
            int64_t off = (int64_t)(v >> 4) * bpp;
            if (off == 0 || off > d) return -1;
            if (d + count > dst_len) count = dst_len - d;
            for (int64_t k = 0; k < count; k++)
                dst[d + k] = dst[d - off + k];
            d += count;
        }
    }
    return d;
}

/* ------------------------------------------------------------------------ */
/* NWA                                                                       */
/* ------------------------------------------------------------------------ */

typedef struct {
    const uint8_t *b;
    int64_t len, pos;
    uint32_t cache;
    int n;
} Bits;

static int get_bits(Bits *r, int k)
{
    while (r->n < k) {
        uint32_t byte = r->pos < r->len ? r->b[r->pos] : 0;
        r->pos++;
        r->cache |= byte << r->n;
        r->n += 8;
    }
    int v = r->cache & ((1u << k) - 1);
    r->cache >>= k;
    r->n -= k;
    return v;
}

/*
 * Decodifica todos los bloques de un NWA comprimido. src es el archivo entero; offsets[] los
 * inicios de bloque. Escribe PCM de 8 o 16 bits en dst.
 */
EXPORT int64_t nwa_decode(const uint8_t *src, int64_t src_len, const uint32_t *offsets, int blocks,
                          int channels, int bps, int complevel, int rle, int block_size,
                          int final_size, uint8_t *dst, int64_t dst_len)
{
    int64_t d = 0;
    if (channels < 1 || channels > 2 || (bps != 8 && bps != 16) || complevel < 0 || complevel > 5) return -1;
    for (int blk = 0; blk < blocks; blk++) {
        int size = (blk == blocks - 1 && final_size > 0) ? final_size : block_size;
        int64_t p = offsets[blk];
        int16_t smp[2] = {0, 0};
        for (int c = 0; c < channels; c++) {
            if (bps == 8) {
                if (p + 1 > src_len) return -2;
                smp[c] = src[p];
                p += 1;
            } else {
                if (p + 2 > src_len) return -2;
                smp[c] = (int16_t)(src[p] | (src[p + 1] << 8));
                p += 2;
            }
        }
        Bits r = {src, src_len, p, 0, 0};
        int ch = 0, rep = 0;
        for (int i = 0; i < size; i++) {
            if (rep == 0) {
                int ctl = get_bits(&r, 3);
                if (ctl == 7) {
                    if (get_bits(&r, 1)) {
                        smp[ch] = 0;
                    } else {
                        int nb = 8, sh = 9;
                        if (complevel < 3) { nb -= complevel; sh += complevel; }
                        int sign = 1 << (nb - 1);
                        int v = get_bits(&r, nb);
                        int delta = (v & (sign - 1)) << sh;
                        smp[ch] = (int16_t)((v & sign) ? smp[ch] - delta : smp[ch] + delta);
                    }
                } else if (ctl) {
                    int nb, sh;
                    if (complevel < 3) { nb = 5 - complevel; sh = 2 + ctl + complevel; }
                    else { nb = 3 + complevel; sh = 1 + ctl; }
                    int sign = 1 << (nb - 1);
                    int v = get_bits(&r, nb);
                    int delta = (v & (sign - 1)) << sh;
                    smp[ch] = (int16_t)((v & sign) ? smp[ch] - delta : smp[ch] + delta);
                } else if (rle) {
                    rep = get_bits(&r, 1);
                    if (rep) {
                        rep = get_bits(&r, 2);
                        if (rep == 3) rep = get_bits(&r, 8);
                    }
                }
            } else {
                rep--;
            }
            if (bps == 8) {
                if (d + 1 > dst_len) return d;
                dst[d++] = (uint8_t)smp[ch];
            } else {
                if (d + 2 > dst_len) return d;
                dst[d++] = (uint8_t)(smp[ch] & 0xff);
                dst[d++] = (uint8_t)((smp[ch] >> 8) & 0xff);
            }
            if (channels == 2) ch ^= 1;
        }
    }
    return d;
}
