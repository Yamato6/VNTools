/*
 * _krkr.c - Bucles lentos de KiriKiri Tools (decodificación de imágenes TLG5 y TLG6)
 *
 * TLG5/6: Copyright (C) 2000-2005 W.Dee and contributors (KiriKiri 2), según el port a C#
 * de GARbro (ArcFormats/KiriKiri/ImageTLG.cs, MIT).
 *
 * Compilar:  gcc -O2 -shared -o _krkr.dll _krkr.c
 * La salida siempre es BGRA de 32 bits (width * height * 4 bytes).
 * Las funciones devuelven 0 si todo va bien o un número negativo si los datos están mal.
 */

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif

/* ------------------------------------------------------------------------ */
/* LZSS de TLG5 (también lo usan los tipos de filtro de TLG6)                */
/* ------------------------------------------------------------------------ */

static int lzss_slide(uint8_t *out, int64_t out_size, const uint8_t *in, int64_t in_size,
                      uint8_t *text, int *pr)
{
    int r = *pr;
    uint32_t flags = 0;
    int64_t i = 0, o = 0;
    while (i < in_size) {
        if (((flags >>= 1) & 256) == 0)
            flags = in[i++] | 0xff00;
        if (flags & 1) {
            if (i + 1 >= in_size) return -1;
            int mpos = in[i] | ((in[i + 1] & 0xf) << 8);
            int mlen = ((in[i + 1] & 0xf0) >> 4) + 3;
            i += 2;
            if (mlen == 18) {
                if (i >= in_size) return -1;
                mlen += in[i++];
            }
            while (mlen--) {
                if (o >= out_size) return -1;
                out[o++] = text[r++] = text[mpos++];
                mpos &= 4095;
                r &= 4095;
            }
        } else {
            if (o >= out_size) return -1;
            uint8_t c = in[i++];
            out[o++] = c;
            text[r++] = c;
            r &= 4095;
        }
    }
    *pr = r;
    return 0;
}

static uint32_t rd32(const uint8_t *p) { return p[0] | (p[1] << 8) | (p[2] << 16) | ((uint32_t)p[3] << 24); }

/* ------------------------------------------------------------------------ */
/* TLG5                                                                      */
/* ------------------------------------------------------------------------ */

/* src: los datos tras el campo blockheight (empieza por la tabla de tamaños de bloque) */
EXPORT int tlg5_decode(const uint8_t *src, int64_t src_len, int width, int height, int colors,
                       int blockheight, uint8_t *out)
{
    if (width <= 0 || height <= 0 || blockheight <= 0 || (colors != 3 && colors != 4)) return -1;
    int64_t blockcount = (height - 1) / blockheight + 1;
    int64_t pos = blockcount * 4;
    int64_t bufsize = (int64_t)blockheight * width + 10;
    uint8_t *buf[4] = {0};
    uint8_t text[4096];
    int r = 0, ret = 0;
    int64_t stride = (int64_t)width * 4;
    memset(text, 0, sizeof text);
    for (int c = 0; c < colors; c++) {
        buf[c] = (uint8_t *)calloc(bufsize, 1);
        if (!buf[c]) { ret = -2; goto done; }
    }
    for (int yb = 0; yb < height; yb += blockheight) {
        for (int c = 0; c < colors; c++) {
            if (pos + 5 > src_len) { ret = -3; goto done; }
            uint8_t mark = src[pos];
            int64_t size = rd32(src + pos + 1);
            pos += 5;
            if (pos + size > src_len) { ret = -3; goto done; }
            if (mark == 0) {
                if (lzss_slide(buf[c], bufsize, src + pos, size, text, &r) < 0) { ret = -4; goto done; }
            } else {
                if (size > bufsize) { ret = -4; goto done; }
                memcpy(buf[c], src + pos, size);
            }
            pos += size;
        }
        int ylim = yb + blockheight < height ? yb + blockheight : height;
        int64_t bpos = 0;
        for (int y = yb; y < ylim; y++, bpos += width) {
            uint8_t *cur = out + y * stride;
            const uint8_t *up = y > 0 ? cur - stride : NULL;
            uint8_t pc0 = 0, pc1 = 0, pc2 = 0, pc3 = 0;
            for (int x = 0; x < width; x++) {
                uint8_t c0 = buf[0][bpos + x], c1 = buf[1][bpos + x], c2 = buf[2][bpos + x];
                c0 += c1; c2 += c1;
                pc0 += c0; pc1 += c1; pc2 += c2;
                uint8_t *p = cur + x * 4;
                if (colors == 4) pc3 += buf[3][bpos + x];
                if (up) {
                    p[0] = pc0 + up[x * 4];
                    p[1] = pc1 + up[x * 4 + 1];
                    p[2] = pc2 + up[x * 4 + 2];
                    p[3] = colors == 4 ? (uint8_t)(pc3 + up[x * 4 + 3]) : 0xff;
                } else {
                    p[0] = pc0; p[1] = pc1; p[2] = pc2;
                    p[3] = colors == 4 ? pc3 : 0xff;
                }
            }
        }
    }
done:
    for (int c = 0; c < 4; c++) free(buf[c]);
    return ret;
}

/* ------------------------------------------------------------------------ */
/* TLG6                                                                      */
/* ------------------------------------------------------------------------ */

#define W_BLOCK 8
#define H_BLOCK 8
#define GOLOMB_N 4
#define LZ_BITS 12
#define LZ_SIZE (1 << LZ_BITS)

static uint8_t lz_table[LZ_SIZE];
static int8_t golomb_table[GOLOMB_N * 2 * 128][GOLOMB_N];
static int tables_ready = 0;

static void init_tables(void)
{
    static const short compressed[GOLOMB_N][9] = {
        {3, 7, 15, 27, 63, 108, 223, 448, 130},
        {3, 5, 13, 24, 51, 95, 192, 384, 257},
        {2, 5, 12, 21, 39, 86, 155, 320, 384},
        {2, 3, 9, 18, 33, 61, 129, 258, 511},
    };
    if (tables_ready) return;
    for (int i = 0; i < LZ_SIZE; i++) {
        int cnt = 0, j;
        for (j = 1; j != LZ_SIZE && !(i & j); j <<= 1, cnt++);
        cnt++;
        if (j == LZ_SIZE) cnt = 0;
        lz_table[i] = (uint8_t)cnt;
    }
    for (int n = 0; n < GOLOMB_N; n++) {
        int a = 0;
        for (int i = 0; i < 9; i++)
            for (int j = 0; j < compressed[n][i]; j++)
                golomb_table[a++][n] = (int8_t)i;
    }
    tables_ready = 1;
}

/* Lector del depósito de bits (pool tiene al menos 8 bytes de relleno al final) */
typedef struct { const uint8_t *pool; int64_t len; int64_t idx; int pos; } Bits;

static uint32_t peek(Bits *b) { return b->idx + 4 <= b->len ? rd32(b->pool + b->idx) : 0; }

static void advance(Bits *b, int n) { b->pos += n; b->idx += b->pos >> 3; b->pos &= 7; }

static int golomb_values(uint32_t *pixelbuf, int pixel_count, Bits *bits, int first, int offset)
{
    uint32_t mask = ~((uint32_t)0xff << offset);
    int n = GOLOMB_N - 1, a = 0;
    bits->idx = 0;
    bits->pos = 1;
    int zero = (bits->pool[0] & 1) == 0;
    for (int pixel = 0; pixel < pixel_count;) {
        int count, bit_count, b;
        uint32_t t = peek(bits) >> bits->pos;
        b = lz_table[t & (LZ_SIZE - 1)];
        bit_count = b;
        while (!b) {
            if (bits->idx > bits->len) return -1;
            bit_count += LZ_BITS;
            advance(bits, LZ_BITS);
            t = peek(bits) >> bits->pos;
            b = lz_table[t & (LZ_SIZE - 1)];
            bit_count += b;
        }
        advance(bits, b);
        bit_count--;
        if (bit_count < 0 || bit_count > 30) return -1;
        count = 1 << bit_count;
        count += (peek(bits) >> bits->pos) & (count - 1);
        advance(bits, bit_count);
        if (pixel + count > pixel_count) return -1;
        if (zero) {
            do {
                if (first) pixelbuf[pixel] = 0; else pixelbuf[pixel] &= mask;
                pixel++;
            } while (--count);
        } else {
            do {
                int k = golomb_table[a][n], v, sign;
                t = peek(bits) >> bits->pos;
                if (t) {
                    b = lz_table[t & (LZ_SIZE - 1)];
                    bit_count = b;
                    while (!b) {
                        if (bits->idx > bits->len) return -1;
                        bit_count += LZ_BITS;
                        advance(bits, LZ_BITS);
                        t = peek(bits) >> bits->pos;
                        b = lz_table[t & (LZ_SIZE - 1)];
                        bit_count += b;
                    }
                    bit_count--;
                } else {
                    bits->idx += 5;
                    if (bits->idx > bits->len) return -1;
                    bit_count = bits->pool[bits->idx - 1];
                    bits->pos = 0;
                    t = peek(bits);
                    b = 0;
                }
                v = (int)((bit_count << k) + ((t >> b) & ((1 << k) - 1)));
                sign = (v & 1) - 1;
                v >>= 1;
                a += v;
                if (a >= GOLOMB_N * 2 * 128) a = GOLOMB_N * 2 * 128 - 1;
                uint8_t val = (uint8_t)((v ^ sign) + sign + 1);
                if (first) pixelbuf[pixel] = val;
                else pixelbuf[pixel] = (pixelbuf[pixel] & mask) | ((uint32_t)val << offset);
                pixel++;
                advance(bits, b + k);
                if (--n < 0) { a >>= 1; n = GOLOMB_N - 1; }
            } while (--count);
        }
        zero = !zero;
    }
    return 0;
}

static uint32_t make_gt_mask(uint32_t a, uint32_t b)
{
    uint32_t tmp2 = ~b;
    uint32_t tmp = ((a & tmp2) + (((a ^ tmp2) >> 1) & 0x7f7f7f7f)) & 0x80808080;
    return ((tmp >> 7) + 0x7f7f7f7f) ^ 0x7f7f7f7f;
}

static uint32_t packed_add(uint32_t a, uint32_t b)
{
    uint32_t tmp = (((a & b) << 1) + ((a ^ b) & 0xfefefefe)) & 0x01010100;
    return a + b - tmp;
}

static uint32_t med2(uint32_t a, uint32_t b, uint32_t c)
{
    uint32_t aa_gt_bb = make_gt_mask(a, b);
    uint32_t x = (a ^ b) & aa_gt_bb;
    uint32_t aa = x ^ a, bb = x ^ b;
    uint32_t n = make_gt_mask(c, bb), nn = make_gt_mask(aa, c);
    uint32_t m = ~(n | nn);
    return (n & aa) | (nn & bb) | ((bb & m) - (c & m) + (aa & m));
}

static uint32_t avg(uint32_t a, uint32_t b)
{
    return (a & b) + (((a ^ b) & 0xfefefefe) >> 1) + ((a ^ b) & 0x01010101);
}

/* Filtro de correlación de color: v (B, G, R, A en bytes) → valores reales */
static uint32_t chroma(int f, uint32_t v)
{
    uint32_t B = v & 0xff, G = (v >> 8) & 0xff, R = (v >> 16) & 0xff, A = v & 0xff000000;
    uint32_t r, g, b;
    switch (f >> 1) {
    case 0:  b = B;             g = G;             r = R; break;
    case 1:  b = B + G;         g = G;             r = R + G; break;
    case 2:  b = B;             g = G + B;         r = R + B + G; break;
    case 3:  b = B + R + G;     g = G + R;         r = R; break;
    case 4:  b = B + R;         g = G + B + R;     r = R + B + R + G; break;
    case 5:  b = B + R;         g = G + B + R;     r = R; break;
    case 6:  b = B + G;         g = G;             r = R; break;
    case 7:  b = B;             g = G + B;         r = R; break;
    case 8:  b = B;             g = G;             r = R + G; break;
    case 9:  b = B + G + R + B; g = G + R + B;     r = R + B; break;
    case 10: b = B + R;         g = G + R;         r = R; break;
    case 11: b = B;             g = G + B;         r = R + B; break;
    case 12: b = B;             g = G + R + B;     r = R + B; break;
    case 13: b = B + G;         g = G + R + B + G; r = R + B + G; break;
    case 14: b = B + G + R;     g = G + R;         r = R + B + G + R; break;
    default: b = B;             g = G + (B << 1);  r = R + (B << 1); break;
    }
    return A | ((r & 0xff) << 16) | ((g & 0xff) << 8) | (b & 0xff);
}

static void decode_line(const uint32_t *prev, uint32_t *cur, int width, int start_block, int block_limit,
                        const uint8_t *filters, int skipbytes, const uint32_t *in, uint32_t initialp,
                        int oddskip, int dir)
{
    uint32_t p, up;
    if (start_block) {
        prev += start_block * W_BLOCK;
        cur += start_block * W_BLOCK;
        p = cur[-1];
        up = prev[-1];
    } else {
        p = up = initialp;
    }
    in += skipbytes * start_block;
    int step = (dir & 1) ? 1 : -1;
    for (int i = start_block; i < block_limit; i++) {
        int w = width - i * W_BLOCK;
        if (w > W_BLOCK) w = W_BLOCK;
        int ww = w;
        if (step == -1) in += ww - 1;
        if (i & 1) in += oddskip * ww;
        int f = filters[i];
        do {
            uint32_t u = *prev;
            uint32_t v = chroma(f, *in);
            p = (f & 1) ? packed_add(avg(p, u), v) : packed_add(med2(p, u, up), v);
            up = u;
            *cur++ = p;
            prev++;
            in += step;
        } while (--w);
        if (step == 1) in += skipbytes - ww;
        else in += skipbytes + 1;
        if (i & 1) in -= oddskip * ww;
    }
}

/* src: los datos tras width/height (empieza por max_bit_length) */
EXPORT int tlg6_decode(const uint8_t *src, int64_t src_len, int width, int height, int colors, uint8_t *out)
{
    if (width <= 0 || height <= 0 || (colors != 1 && colors != 3 && colors != 4)) return -1;
    init_tables();
    int64_t pos = 0;
    if (src_len < 8) return -3;
    int64_t max_bit_length = rd32(src);
    pos += 4;
    int x_blocks = (width - 1) / W_BLOCK + 1, y_blocks = (height - 1) / H_BLOCK + 1;
    int main_count = width / W_BLOCK, fraction = width - main_count * W_BLOCK;
    int ret = 0;
    uint32_t *image = (uint32_t *)out;
    int64_t pool_size = max_bit_length / 8 + 16;
    uint8_t *pool = (uint8_t *)calloc(pool_size, 1);
    uint32_t *pixelbuf = (uint32_t *)calloc((size_t)width * H_BLOCK + 1, 4);
    uint8_t *filters = (uint8_t *)calloc((size_t)x_blocks * y_blocks + 64, 1);
    uint32_t *zeroline = (uint32_t *)calloc(width, 4);
    uint8_t text[4096];
    if (!pool || !pixelbuf || !filters || !zeroline) { ret = -2; goto done; }
    uint32_t zerocolor = colors == 3 ? 0xff000000 : 0;
    for (int i = 0; i < width; i++) zeroline[i] = zerocolor;
    {
        int p = 0;
        for (uint32_t i = 0; i < 32; i++)
            for (uint32_t j = 0; j < 16; j++) {
                text[p++] = i; text[p++] = i; text[p++] = i; text[p++] = i;
                text[p++] = j; text[p++] = j; text[p++] = j; text[p++] = j;
            }
        int64_t size = rd32(src + pos);
        pos += 4;
        if (pos + size > src_len) { ret = -3; goto done; }
        int r = 0;
        if (lzss_slide(filters, (int64_t)x_blocks * y_blocks + 64, src + pos, size, text, &r) < 0) { ret = -4; goto done; }
        pos += size;
    }
    const uint32_t *prevline = zeroline;
    for (int y = 0; y < height; y += H_BLOCK) {
        int ylim = y + H_BLOCK < height ? y + H_BLOCK : height;
        int pixel_count = (ylim - y) * width;
        for (int c = 0; c < colors; c++) {
            if (pos + 4 > src_len) { ret = -3; goto done; }
            uint32_t bit_length = rd32(src + pos);
            pos += 4;
            int method = (bit_length >> 30) & 3;
            bit_length &= 0x3fffffff;
            int64_t byte_length = (bit_length + 7) / 8;
            if (method != 0) { ret = -5; goto done; }
            if (pos + byte_length > src_len || byte_length + 8 > pool_size) { ret = -3; goto done; }
            memcpy(pool, src + pos, byte_length);
            memset(pool + byte_length, 0, pool_size - byte_length);
            pos += byte_length;
            Bits bits = {pool, byte_length + 8, 0, 1};
            int first = c == 0 && colors != 1;
            if (golomb_values(pixelbuf, pixel_count, &bits, first, first ? 0 : c * 8) < 0) { ret = -4; goto done; }
        }
        const uint8_t *ft = filters + (y / H_BLOCK) * x_blocks;
        int skipbytes = (ylim - y) * W_BLOCK;
        for (int yy = y; yy < ylim; yy++) {
            uint32_t *cur = image + (int64_t)yy * width;
            int dir = (yy & 1) ^ 1;
            int oddskip = (ylim - yy - 1) - (yy - y);
            if (main_count) {
                int start = (width < W_BLOCK ? width : W_BLOCK) * (yy - y);
                decode_line(prevline, cur, width, 0, main_count, ft, skipbytes, pixelbuf + start,
                            zerocolor, oddskip, dir);
            }
            if (main_count != x_blocks) {
                int ww = fraction > W_BLOCK ? W_BLOCK : fraction;
                int start = ww * (yy - y);
                decode_line(prevline, cur, width, main_count, x_blocks, ft, skipbytes, pixelbuf + start,
                            zerocolor, oddskip, dir);
            }
            prevline = cur;
        }
    }
    if (colors == 1) {
        for (int64_t i = 0; i < (int64_t)width * height; i++) {
            uint32_t b = image[i] & 0xff;
            image[i] = 0xff000000 | (b << 16) | (b << 8) | b;
        }
    } else if (colors == 3) {
        for (int64_t i = 0; i < (int64_t)width * height; i++) image[i] |= 0xff000000;
    }
done:
    free(pool); free(pixelbuf); free(filters); free(zeroline);
    return ret;
}
