/* CatSystem2 Tools en C (opcional, mucho más rápido que la versión Python):
   - Blowfish ECB con bloques de dos u32 little-endian (cifrado de los .int)
   - Núcleo de las imágenes HG-3 img0000 (ceros, planos y deltas), en los dos sentidos
   Se compila con: gcc -O2 -shared -static-libgcc -o _cs2.dll _cs2.c */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif

/* ------------------------------------------------------------------ Blowfish */
/* st: 18 u32 de P y 4 x 256 u32 de S, ya preparados con la clave */

#define BF_F(x) ((((S[(x) >> 24] + S[256 + (((x) >> 16) & 255)]) ^ S[512 + (((x) >> 8) & 255)]) \
                  + S[768 + ((x) & 255)]))

static uint32_t rd32(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static void wr32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24);
}

EXPORT void bf_decipher(const uint32_t *st, uint8_t *buf, int64_t len) {
    const uint32_t *P = st, *S = st + 18;
    int64_t n = len & ~(int64_t)7;
    for (int64_t i = 0; i < n; i += 8) {
        uint32_t l = rd32(buf + i), r = rd32(buf + i + 4), t;
        for (int k = 17; k > 1; k--) {
            l ^= P[k];
            r ^= BF_F(l);
            t = l; l = r; r = t;
        }
        t = l; l = r; r = t;
        r ^= P[1];
        l ^= P[0];
        wr32(buf + i, l);
        wr32(buf + i + 4, r);
    }
}

EXPORT void bf_encipher(const uint32_t *st, uint8_t *buf, int64_t len) {
    const uint32_t *P = st, *S = st + 18;
    int64_t n = len & ~(int64_t)7;
    for (int64_t i = 0; i < n; i += 8) {
        uint32_t l = rd32(buf + i), r = rd32(buf + i + 4), t;
        for (int k = 0; k < 16; k++) {
            l ^= P[k];
            r ^= BF_F(l);
            t = l; l = r; r = t;
        }
        t = l; l = r; r = t;
        r ^= P[16];
        l ^= P[17];
        wr32(buf + i, l);
        wr32(buf + i + 4, r);
    }
}

/* ------------------------------------------------------------------ HG-3 img0000 */

typedef struct { const uint8_t *p; int64_t len, pos; int bit; } BitReader;

static int get_bit(BitReader *b) {
    if (b->pos >= b->len) return -1;
    int v = (b->p[b->pos] >> b->bit) & 1;
    if (++b->bit == 8) { b->bit = 0; b->pos++; }
    return v;
}

/* Longitud en código Elias-gamma: n ceros, un 1 y n bits más */
static int64_t get_count(BitReader *b) {
    int n = 0, v;
    while ((v = get_bit(b)) == 0)
        if (++n >= 32) return -1;
    if (v < 0) return -1;
    int64_t value = 1;
    while (n-- > 0) {
        if ((v = get_bit(b)) < 0) return -1;
        value = (value << 1) | v;
    }
    return value;
}

/* Byte guardado -> byte real y al revés */
static uint8_t conv(uint8_t v) { return (v & 1) ? (uint8_t)((v >> 1) ^ 0xFF) : (uint8_t)(v >> 1); }
static uint8_t unconv(uint8_t c) { return c < 0x80 ? (uint8_t)(c << 1) : (uint8_t)(((c ^ 0xFF) << 1) | 1); }

/* Tamaño de la salida descomprimida (primer código del flujo de control), o -1 */
EXPORT int64_t hg3_raw_size(const uint8_t *ctl, int64_t clen) {
    BitReader b = {ctl, clen, 0, 0};
    if (get_bit(&b) < 0) return -1;
    return get_count(&b);
}

/* data/ctl ya descomprimidos con zlib. out: w*h*ps bytes, filas en el orden del archivo,
   píxeles BGR(A). Devuelve 0 si todo fue bien. */
EXPORT int hg3_unpack(const uint8_t *data, int64_t dlen, const uint8_t *ctl, int64_t clen,
                      uint8_t *out, int w, int h, int ps) {
    BitReader b = {ctl, clen, 0, 0};
    int copy = get_bit(&b);
    if (copy < 0) return 1;
    int64_t raw_size = get_count(&b);
    if (raw_size <= 0) return 2;
    uint8_t *raw = (uint8_t *)calloc((size_t)raw_size, 1);
    if (!raw) return 3;
    int64_t src = 0, dst = 0;
    while (dst < raw_size) {
        int64_t n = get_count(&b);
        if (n <= 0 || dst + n > raw_size) { free(raw); return 4; }
        if (copy) {
            if (src + n > dlen) { free(raw); return 5; }
            memcpy(raw + dst, data + src, (size_t)n);
            src += n;
        }
        dst += n;
        copy = !copy;
    }

    int64_t total = (int64_t)w * h * ps, q = raw_size / 4;
    const uint8_t *p0 = raw, *p1 = raw + q, *p2 = raw + 2 * q, *p3 = raw + 3 * q;
    int64_t o = 0;
    for (int64_t i = 0; i < q && o < total; i++) {
        uint8_t a = p0[i], c = p1[i], d = p2[i], e = p3[i];
        for (int k = 0; k < 4 && o < total; k++) {
            int s = 2 * k;
            uint8_t v = (uint8_t)((((a >> s) & 3) << 6) | (((c >> s) & 3) << 4) |
                                  (((d >> s) & 3) << 2) | ((e >> s) & 3));
            out[o++] = conv(v);
        }
    }
    free(raw);
    if (o < total) memset(out + o, 0, (size_t)(total - o));

    int64_t stride = (int64_t)w * ps;
    for (int64_t x = ps; x < stride; x++)
        out[x] = (uint8_t)(out[x] + out[x - ps]);
    for (int64_t y = 1; y < h; y++) {
        uint8_t *line = out + y * stride, *prev = line - stride;
        for (int64_t x = 0; x < stride; x++)
            line[x] = (uint8_t)(line[x] + prev[x]);
    }
    return 0;
}

typedef struct { uint8_t *p; int64_t pos; int bit; } BitWriter;

static void put_bit(BitWriter *b, int v) {
    if (b->bit == 0) b->p[b->pos] = 0;
    if (v) b->p[b->pos] |= (uint8_t)(1 << b->bit);
    if (++b->bit == 8) { b->bit = 0; b->pos++; }
}

static void put_count(BitWriter *b, int64_t v) {
    int n = 0;
    while ((v >> (n + 1)) != 0) n++;
    for (int i = 0; i < n; i++) put_bit(b, 0);
    for (int i = n; i >= 0; i--) put_bit(b, (int)((v >> i) & 1));
}

/* Inverso de hg3_unpack. pix: w*h*ps bytes BGR(A), filas en el orden del archivo.
   data_out y ctl_out deben tener al menos hg3_pack_bound() bytes. sizes recibe
   {tamaño de datos, tamaño de control, tamaño de la salida descomprimida}. */
EXPORT int64_t hg3_pack_bound(int w, int h, int ps) {
    return ((int64_t)w * h * ps + 3) / 4 * 4 + 64;
}

EXPORT int hg3_pack(const uint8_t *pix, int w, int h, int ps,
                    uint8_t *data_out, uint8_t *ctl_out, int64_t *sizes) {
    int64_t total = (int64_t)w * h * ps, stride = (int64_t)w * ps;
    int64_t raw_size = (total + 3) / 4 * 4, q = raw_size / 4;
    uint8_t *buf = (uint8_t *)calloc((size_t)raw_size, 1);
    uint8_t *raw = (uint8_t *)malloc((size_t)raw_size);
    if (!buf || !raw) { free(buf); free(raw); return 1; }
    memcpy(buf, pix, (size_t)total);

    for (int64_t y = h - 1; y >= 1; y--) {
        uint8_t *line = buf + y * stride, *prev = line - stride;
        for (int64_t x = 0; x < stride; x++)
            line[x] = (uint8_t)(line[x] - prev[x]);
    }
    for (int64_t x = stride - 1; x >= ps; x--)
        buf[x] = (uint8_t)(buf[x] - buf[x - ps]);

    for (int64_t i = 0; i < q; i++) {
        uint8_t a = 0, c = 0, d = 0, e = 0;
        for (int k = 0; k < 4; k++) {
            uint8_t v = unconv(buf[4 * i + k]);
            int s = 2 * k;
            a |= (uint8_t)(((v >> 6) & 3) << s);
            c |= (uint8_t)(((v >> 4) & 3) << s);
            d |= (uint8_t)(((v >> 2) & 3) << s);
            e |= (uint8_t)((v & 3) << s);
        }
        raw[i] = a; raw[q + i] = c; raw[2 * q + i] = d; raw[3 * q + i] = e;
    }
    free(buf);

    BitWriter b = {ctl_out, 0, 0};
    int copy = raw[0] != 0;
    put_bit(&b, copy);
    put_count(&b, raw_size);
    int64_t dpos = 0, i = 0;
    while (i < raw_size) {
        int64_t j = i;
        if (copy) {
            while (j < raw_size && raw[j] != 0) j++;
            memcpy(data_out + dpos, raw + i, (size_t)(j - i));
            dpos += j - i;
        } else {
            while (j < raw_size && raw[j] == 0) j++;
        }
        put_count(&b, j - i);
        i = j;
        copy = !copy;
    }
    free(raw);
    sizes[0] = dpos;
    sizes[1] = b.pos + (b.bit ? 1 : 0);
    sizes[2] = raw_size;
    return 0;
}
