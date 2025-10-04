import java.io.*;
import java.nio.charset.StandardCharsets;

/**
 * InsaniUtils - utilidades binarias (adaptado para aceptar RandomAccessFile)
 * Equivalente Java de la versión Python3 que usabas.
 */
public class CrossUtils {

    public static final int LITTLE_ENDIAN = 1;
    public static final int BIG_ENDIAN = -1;

    public static final int BYTE_LENGTH = 1;
    public static final int SHORT_LENGTH = 2;
    public static final int INT_LENGTH = 4;
    public static final int LONG_LENGTH = 8;

    public static final int ERROR_NONE = 0;
    public static final int ERROR_WARNING = 1;
    public static final int ERROR_ABORT = 2;

    // -------------------- READ UNSIGNED --------------------

    public static long readUnsigned(InputStream in, int size, int endian) throws IOException {
        long result = 0;
        for (int i = 0; i < size; i++) {
            int temp = in.read();
            if (temp == -1) throw new EOFException("Unexpected EOF while reading unsigned integer");
            if (endian == LITTLE_ENDIAN) {
                result |= ((long) temp << (8 * i));
            } else if (endian == BIG_ENDIAN) {
                result = (result << 8) | temp;
            } else {
                throw new IllegalArgumentException("Unknown endian specification");
            }
        }
        return result;
    }

    public static long readUnsigned(RandomAccessFile raf, int size, int endian) throws IOException {
        long result = 0;
        for (int i = 0; i < size; i++) {
            int temp = raf.read();
            if (temp == -1) throw new EOFException("Unexpected EOF while reading unsigned integer");
            if (endian == LITTLE_ENDIAN) {
                result |= ((long) temp << (8 * i));
            } else if (endian == BIG_ENDIAN) {
                result = (result << 8) | temp;
            } else {
                throw new IllegalArgumentException("Unknown endian specification");
            }
        }
        return result;
    }

    // -------------------- WRITE UNSIGNED --------------------

    public static void writeUnsigned(OutputStream out, long value, int size, int endian) throws IOException {
        for (int i = 0; i < size; i++) {
            int temp;
            if (endian == LITTLE_ENDIAN) {
                temp = (int) ((value >> (8 * i)) & 0xFF);
            } else if (endian == BIG_ENDIAN) {
                temp = (int) ((value >> (8 * (size - i - 1))) & 0xFF);
            } else {
                throw new IllegalArgumentException("Unknown endian specification");
            }
            out.write(temp);
        }
    }

    public static void writeUnsigned(RandomAccessFile raf, long value, int size, int endian) throws IOException {
        for (int i = 0; i < size; i++) {
            int temp;
            if (endian == LITTLE_ENDIAN) {
                temp = (int) ((value >> (8 * i)) & 0xFF);
            } else if (endian == BIG_ENDIAN) {
                temp = (int) ((value >> (8 * (size - i - 1))) & 0xFF);
            } else {
                throw new IllegalArgumentException("Unknown endian specification");
            }
            raf.write(temp);
        }
    }

    // -------------------- READ / WRITE STRING (null-terminated) --------------------

    public static String readString(InputStream in) throws IOException {
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        int b;
        while ((b = in.read()) != -1 && b != 0) {
            buffer.write(b);
        }
        return buffer.toString(StandardCharsets.ISO_8859_1.name());
    }

    public static String readString(RandomAccessFile raf) throws IOException {
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        int b;
        while ((b = raf.read()) != -1 && b != 0) {
            buffer.write(b);
        }
        return buffer.toString(StandardCharsets.ISO_8859_1.name());
    }

    public static void writeString(OutputStream out, String value) throws IOException {
        byte[] b = value.getBytes(StandardCharsets.ISO_8859_1);
        out.write(b);
        out.write(0);
    }

    public static void writeString(RandomAccessFile raf, String value) throws IOException {
        byte[] b = value.getBytes(StandardCharsets.ISO_8859_1);
        raf.write(b);
        raf.write(0);
    }

    // -------------------- ESCAPE / UNESCAPE --------------------

    public static String escapeString(String value) {
        StringBuilder result = new StringBuilder();
        for (char c : value.toCharArray()) {
            if (c < 32 || c > 126) {
                result.append(String.format("#%02X", (int) c));
            } else {
                result.append(c);
            }
        }
        return result.toString();
    }

    public static String unescapeString(String value) {
        StringBuilder result = new StringBuilder();
        int i = 0;
        while (i < value.length()) {
            if (value.charAt(i) == '#') {
                if (i + 2 < value.length()) {
                    String hex = value.substring(i + 1, i + 3);
                    int code = Integer.parseInt(hex, 16);
                    result.append((char) code);
                    i += 3;
                } else {
                    // malformed; append rest
                    result.append(value.substring(i));
                    break;
                }
            } else {
                result.append(value.charAt(i));
                i++;
            }
        }
        return result.toString();
    }

    // -------------------- ASSERT STRING / ZEROES --------------------

    public static boolean assertString(InputStream in, String expected, int severity) throws IOException {
        byte[] expectedBytes = expected.getBytes(StandardCharsets.ISO_8859_1);
        byte[] actual = new byte[expectedBytes.length];
        int read = 0;
        while (read < actual.length) {
            int r = in.read(actual, read, actual.length - read);
            if (r == -1) break;
            read += r;
        }
        if (read != expectedBytes.length || !java.util.Arrays.equals(expectedBytes, actual)) {
            if (severity != ERROR_NONE) {
                System.err.printf("Expected \"%s\" but saw \"%s\".%n",
                        escapeString(expected),
                        escapeString(new String(actual, 0, Math.max(read, 0), StandardCharsets.ISO_8859_1)));
            }
            if (severity == ERROR_ABORT) throw new IOException("assertString failed");
            return false;
        }
        return true;
    }

    public static boolean assertString(RandomAccessFile raf, String expected, int severity) throws IOException {
        byte[] expectedBytes = expected.getBytes(StandardCharsets.ISO_8859_1);
        byte[] actual = new byte[expectedBytes.length];
        int read = 0;
        while (read < actual.length) {
            int r = raf.read(actual, read, actual.length - read);
            if (r == -1) break;
            read += r;
        }
        if (read != expectedBytes.length || !java.util.Arrays.equals(expectedBytes, actual)) {
            if (severity != ERROR_NONE) {
                System.err.printf("Expected \"%s\" at position 0x%X but saw \"%s\".%n",
                        escapeString(expected),
                        raf.getFilePointer() - read,
                        escapeString(new String(actual, 0, Math.max(read, 0), StandardCharsets.ISO_8859_1)));
            }
            if (severity == ERROR_ABORT) throw new IOException("assertString failed");
            return false;
        }
        return true;
    }

    public static boolean assertZeroes(InputStream in, int num, int severity) throws IOException {
        byte[] actual = new byte[num];
        int read = 0;
        while (read < num) {
            int r = in.read(actual, read, num - read);
            if (r == -1) break;
            read += r;
        }
        int found_error = -1;
        int badByte = 0;
        for (int i = 0; i < read; i++) {
            if (actual[i] != 0) {
                found_error = i;
                badByte = actual[i] & 0xFF;
                break;
            }
        }
        if (found_error < 0 && read < num) {
            found_error = read;
        }
        if (found_error >= 0) {
            if (severity != ERROR_NONE) {
                System.err.printf("Expected \"\\x00\" at position 0x%X but found \"%s\".%n",
                        found_error, escapeString(String.format("#%02X", badByte)));
            }
            if (severity == ERROR_ABORT) throw new IOException("assertZeroes failed");
            return false;
        }
        return true;
    }

    public static boolean assertZeroes(RandomAccessFile raf, int num, int severity) throws IOException {
        byte[] actual = new byte[num];
        int read = 0;
        while (read < num) {
            int r = raf.read(actual, read, num - read);
            if (r == -1) break;
            read += r;
        }
        long basePos = raf.getFilePointer() - read;
        int found_error = -1;
        int badByte = 0;
        for (int i = 0; i < read; i++) {
            if (actual[i] != 0) {
                found_error = i;
                badByte = actual[i] & 0xFF;
                break;
            }
        }
        if (found_error < 0 && read < num) {
            found_error = read;
        }
        if (found_error >= 0) {
            if (severity != ERROR_NONE) {
                System.err.printf("Expected \"\\x00\" at position 0x%X but found \"%s\".%n",
                        basePos + found_error, escapeString(String.format("#%02X", badByte)));
            }
            if (severity == ERROR_ABORT) throw new IOException("assertZeroes failed");
            return false;
        }
        return true;
    }

    // -------------------- WRITE ZEROES --------------------

    public static void writeZeroes(OutputStream out, int num) throws IOException {
        if (num <= 0) return;
        byte[] block = new byte[1024];
        while (num >= 1024) {
            out.write(block);
            num -= 1024;
        }
        if (num > 0) out.write(new byte[num]);
    }

    public static void writeZeroes(RandomAccessFile raf, int num) throws IOException {
        if (num <= 0) return;
        byte[] block = new byte[1024];
        while (num >= 1024) {
            raf.write(block);
            num -= 1024;
        }
        if (num > 0) raf.write(new byte[num]);
    }

    // -------------------- (Opcional) Main de pruebas --------------------

    public static void main(String[] args) throws Exception {
        // Puedes usar estas pruebas rápidas para validar la clase.
        // (No estrictamente necesario en tu build final).
        System.out.println("InsaniUtils compiled OK.");
    }
}
