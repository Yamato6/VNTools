/*
 * _bgi.c - Bucles lentos de BGI Tools en C (CompressedBG v1 y v2)
 * Port de GARbro ArcFormats/Ethornell/ImageCBG.cs (MIT), igual que bgi_image.py,
 * que conserva la versión en Python como respaldo.
 *
 * gcc -O2 -shared -static-libgcc -o _bgi.dll _bgi.c
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif

/* ------------------------------------------------------------------ bits MSB */

typedef struct {
    const uint8_t *d;
    int64_t end, p;
    int cache, n;
} Bits;

static int bit(Bits *b) {
    if (b->n == 0) {
        if (b->p >= b->end)
            return -1;
        b->cache = b->d[b->p++];
        b->n = 8;
    }
    b->n--;
    return (b->cache >> b->n) & 1;
}

static int64_t bits(Bits *b, int k) {
    int64_t v = 0;
    for (int i = 0; i < k; i++) {
        int x = bit(b);
        if (x < 0)
            return -1;
        v = (v << 1) | x;
    }
    return v;
}

/* ------------------------------------------------------------------ Huffman de CBG */

#define MAX_NODES 1024

typedef struct {
    uint8_t valid, internal;
    uint64_t weight;
    int l, r;
} Node;

typedef struct {
    Node nodes[MAX_NODES];
    int count;
} Tree;

/* Mismo algoritmo que GARbro HuffmanTree (v2 = variante de la versión 2). */
static int build_tree(Tree *t, const uint32_t *weights, int n, int v2) {
    uint64_t total = 0;
    for (int i = 0; i < n; i++) {
        t->nodes[i].valid = weights[i] != 0;
        t->nodes[i].internal = 0;
        t->nodes[i].weight = weights[i];
        t->nodes[i].l = t->nodes[i].r = -1;
        total += weights[i];
    }
    t->count = n;
    for (;;) {
        uint64_t weight = 0;
        int ch[2] = {-1, -1};
        for (int i = 0; i < 2; i++) {
            uint64_t mn = 0xFFFFFFFFu;
            int k = 0;
            if (v2) {
                while (k < t->count) {
                    if (t->nodes[k].valid) {
                        mn = t->nodes[k].weight;
                        ch[i] = k++;
                        break;
                    }
                    k++;
                }
                if (k < i + 1)
                    k = i + 1;
            }
            for (; k < t->count; k++) {
                if (t->nodes[k].valid && t->nodes[k].weight < mn) {
                    mn = t->nodes[k].weight;
                    ch[i] = k;
                }
            }
            if (ch[i] == -1)
                continue;
            t->nodes[ch[i]].valid = 0;
            weight += t->nodes[ch[i]].weight;
        }
        if (t->count >= MAX_NODES)
            return -1;
        Node *nd = &t->nodes[t->count++];
        nd->valid = 1;
        nd->internal = 1;
        nd->weight = weight;
        nd->l = ch[0];
        nd->r = ch[1];
        if (weight >= total)
            return 0;
    }
}

static int decode_sym(const Tree *t, Bits *b) {
    int n = t->count - 1;
    for (;;) {
        int x = bit(b);
        if (x < 0)
            return -1;
        n = x ? t->nodes[n].r : t->nodes[n].l;
        if (n < 0)
            return -1;
        if (!t->nodes[n].internal)
            return n;
    }
}

static int64_t varint(const uint8_t *d, int64_t len, int64_t *p) {
    int64_t v = 0;
    int sh = 0;
    while (*p < len) {
        uint8_t c = d[(*p)++];
        v |= (int64_t)(c & 0x7F) << sh;
        sh += 7;
        if (!(c & 0x80))
            return v;
    }
    return -1;
}

/* ------------------------------------------------------------------ CBG v1 */

/* Decodifica una imagen CBG v1 en out (w*h*ps bytes, BGR(A) o gris). 0 = bien. */
EXPORT int cbg_v1(const uint8_t *src, int64_t srclen, int64_t pos, const uint32_t *weights,
                  int64_t inter_len, uint8_t *out, int w, int h, int ps) {
    Tree *t = malloc(sizeof(Tree));
    uint8_t *packed = malloc(inter_len > 0 ? inter_len : 1);
    if (!t || !packed) {
        free(t);
        free(packed);
        return -3;
    }
    int rc = 0;
    if (build_tree(t, weights, 256, 0) < 0) {
        rc = -2;
        goto done;
    }
    Bits b = {src, srclen, pos, 0, 0};
    for (int64_t i = 0; i < inter_len; i++) {
        int s = decode_sym(t, &b);
        if (s < 0) {
            rc = -1;
            goto done;
        }
        packed[i] = (uint8_t)s;
    }
    int64_t size = (int64_t)w * h * ps, dst = 0, sp = 0;
    int zero = 0;
    memset(out, 0, size);
    while (dst < size && sp < inter_len) {
        int64_t cnt = varint(packed, inter_len, &sp);
        if (cnt < 0 || dst + cnt > size)
            break;
        if (!zero) {
            if (sp + cnt > inter_len)
                cnt = inter_len - sp;
            memcpy(out + dst, packed + sp, cnt);
            sp += cnt;
        }
        dst += cnt;
        zero = !zero;
    }
    /* Promedio inverso: cada canal suma la media del vecino izquierdo y el de arriba */
    int64_t stride = (int64_t)w * ps;
    for (int y = 0; y < h; y++) {
        uint8_t *row = out + y * stride;
        uint8_t *up = y ? row - stride : NULL;
        for (int x = 0; x < w; x++) {
            for (int c = 0; c < ps; c++) {
                int avg;
                if (x && y)
                    avg = (row[(x - 1) * ps + c] + up[x * ps + c]) / 2;
                else if (x)
                    avg = row[(x - 1) * ps + c];
                else if (y)
                    avg = up[x * ps + c];
                else
                    continue;
                row[x * ps + c] = (uint8_t)(row[x * ps + c] + avg);
            }
        }
    }
done:
    free(t);
    free(packed);
    return rc;
}

/* ------------------------------------------------------------------ CBG v2 */

static const int FILL[64] = {
    0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5, 12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7,
    14, 21, 28, 35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51, 58, 59, 52, 45, 38, 31, 39, 46,
    53, 60, 61, 54, 47, 55, 62, 63};

static int v2_row(const Tree *tree1, const Tree *tree2, const uint8_t *data, int64_t end, int64_t pos,
                  int size, int32_t *color);

/* Coeficientes DCT de una fila de bloques (antes de la IDCT), en color[size]. w1: 16 pesos del
   árbol de DC, w2: 176 del de AC. */
EXPORT int cbg_v2_row(const uint8_t *data, int64_t end, int64_t pos, int size, int32_t *color,
                      const uint32_t *w1, const uint32_t *w2) {
    Tree *t = malloc(2 * sizeof(Tree));
    if (!t)
        return -3;
    int rc = -2;
    if (build_tree(&t[0], w1, 0x10, 1) == 0 && build_tree(&t[1], w2, 0xB0, 1) == 0)
        rc = v2_row(&t[0], &t[1], data, end, pos, size, color);
    free(t);
    return rc;
}

static int v2_row(const Tree *tree1, const Tree *tree2, const uint8_t *data, int64_t end, int64_t pos,
                  int size, int32_t *color) {
    Bits b = {data, end, pos, 0, 0};
    int32_t acc = 0;
    memset(color, 0, sizeof(int32_t) * size);
    for (int i = 0; i < size; i += 64) {
        if (b.p >= end && b.n == 0)
            break;
        int cnt = decode_sym(tree1, &b);
        if (cnt < 0)
            return -1;
        if (cnt) {
            int64_t v = bits(&b, cnt);
            if ((v >> (cnt - 1)) == 0)
                v = (int64_t)(((uint64_t)-1 << cnt) | (uint64_t)v) + 1;
            acc += (int32_t)v;
        }
        color[i] = acc;
    }
    b.n = 0;    /* alinear al byte */
    for (int i = 0; i < size; i += 64) {
        if (b.p >= end)
            break;
        int idx = 1;
        while (idx < 64 && b.p < end) {
            int code = decode_sym(tree2, &b);
            if (code < 0)
                return -1;
            if (code == 0)
                break;
            if (code == 0xF) {
                idx += 0x10;
                continue;
            }
            idx += code & 0xF;
            if (idx >= 64)
                break;
            code >>= 4;
            int64_t v = bits(&b, code);
            if (code && (v >> (code - 1)) == 0)
                v = (int64_t)(((uint64_t)-1 << code) | (uint64_t)v) + 1;
            if (i + FILL[idx] < size)
                color[i + FILL[idx]] = (int16_t)v;
            idx++;
        }
    }
    return 0;
}

/* Canal alfa de v2: LZ con distancias en 2D sobre los píxeles BGRA de flat (n bytes). */
EXPORT int cbg_alpha(const uint8_t *data, int64_t len, int64_t p, uint8_t *flat, int64_t n, int W) {
    int64_t dst = 3;
    int ctl = 2;
    while (dst < n) {
        ctl >>= 1;
        if (ctl == 1) {
            if (p >= len)
                return 0;
            ctl = data[p++] | 0x100;
        }
        if (ctl & 1) {
            if (p + 1 >= len)
                return 0;
            int v = data[p] | (data[p + 1] << 8);
            p += 2;
            int x = v & 0x3F;
            if (x > 0x1F)
                x |= -0x40;
            int y = (v >> 6) & 7;
            if (y)
                y |= -8;
            int cnt = ((v >> 9) & 0x7F) + 3;
            int64_t src = dst + ((int64_t)x + (int64_t)y * W) * 4;
            if (src < 0 || src >= dst)
                return 0;
            for (int k = 0; k < cnt && dst < n; k++) {
                flat[dst] = flat[src];
                src += 4;
                dst += 4;
            }
        } else {
            if (p >= len)
                return 0;
            flat[dst] = data[p++];
            dst += 4;
        }
    }
    return 0;
}
