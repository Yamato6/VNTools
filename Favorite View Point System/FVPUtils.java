import java.io.*;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;

/**
 * Utilidades para leer y escribir archivos binarios FVP en formato Little Endian.
 */
public class FVPUtils implements Closeable {
    private final RandomAccessFile file;

    public FVPUtils(String path, String mode) throws IOException {
        this.file = new RandomAccessFile(path, mode);
    }

    @Override
    public void close() throws IOException {
        file.close();
    }

    // === Utilidades de lectura y escritura ===

    public void skip(long bytes) throws IOException {
        file.seek(file.getFilePointer() + bytes);
    }

    public byte[] readUntilZero() throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        int b;
        while ((b = file.read()) != -1 && b != 0) {
            out.write(b);
        }
        return out.toByteArray();
    }

    public byte[] readUntilEnd() throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buffer = new byte[4096];
        int n;
        while ((n = file.read(buffer)) != -1) {
            out.write(buffer, 0, n);
        }
        return out.toByteArray();
    }

    public int readU16LE() throws IOException {
        byte[] bytes = new byte[2];
        file.readFully(bytes);
        return ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN).getShort() & 0xFFFF;
    }

    public long readU32LE() throws IOException {
        byte[] bytes = new byte[4];
        file.readFully(bytes);
        return ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN).getInt() & 0xFFFFFFFFL;
    }

    public void writeU16LE(int x) throws IOException {
        ByteBuffer buffer = ByteBuffer.allocate(2).order(ByteOrder.LITTLE_ENDIAN);
        buffer.putShort((short) x);
        file.write(buffer.array());
    }

    public void writeU32LE(long x) throws IOException {
        ByteBuffer buffer = ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN);
        buffer.putInt((int) x);
        file.write(buffer.array());
    }

    public void write(byte[] data) throws IOException {
        file.write(data);
    }

    public void writeByte(int b) throws IOException {
        file.write(b);
    }

    public void seek(long pos) throws IOException {
        file.seek(pos);
    }

    public long tell() throws IOException {
        return file.getFilePointer();
    }

    public long length() throws IOException {
        return file.length();
    }

    // --- Métodos añadidos para compatibilidad con BINTool ---

    /**
     * Lee completamente el arreglo provisto (delegando a RandomAccessFile.readFully).
     */
    public void readFully(byte[] b) throws IOException {
        file.readFully(b);
    }

    /**
     * Lee len bytes en b desde offset off.
     */
    public void readFully(byte[] b, int off, int len) throws IOException {
        file.readFully(b, off, len);
    }

    /**
     * Lee 'length' bytes y devuelve un nuevo arreglo con esos bytes.
     * Lanza EOFException si no hay suficientes bytes.
     */
    public byte[] readBytes(int length) throws IOException {
        if (length < 0) throw new IllegalArgumentException("length < 0");
        if (length == 0) return new byte[0];
        if (length > Integer.MAX_VALUE) throw new IllegalArgumentException("length too large");
        byte[] b = new byte[length];
        file.readFully(b);
        return b;
    }
}
