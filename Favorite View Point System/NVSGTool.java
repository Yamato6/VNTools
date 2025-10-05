import java.awt.image.BufferedImage;
import java.awt.image.DataBufferByte;
import java.io.*;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.zip.Deflater;
import java.util.zip.Inflater;
import javax.imageio.ImageIO;

public class NVSGTool {

    private static final byte[] HZC1_MAGIC = "hzc1".getBytes();
    private static final byte[] NVSG_MAGIC = "NVSG".getBytes();

    public static void main(String[] args) {
        if (args.length < 3) {
            printUsage();
            return;
        }

        String mode = args[0];
        File input = new File(args[1]);
        File output = new File(args[2]);

        int x = -1, y = -1, imageCount = 1;

        for (int i = 3; i < args.length; i++) {
            if (args[i].equals("--x") && i + 1 < args.length)
                x = Integer.parseInt(args[++i]);
            else if (args[i].equals("--y") && i + 1 < args.length)
                y = Integer.parseInt(args[++i]);
            else if (args[i].equals("--image_count") && i + 1 < args.length)
                imageCount = Integer.parseInt(args[++i]);
        }

        try {
            if (mode.equals("--decode")) {
                convertToPng(input, output);
            } else if (mode.equals("--encode")) {
                if (x < 0 || y < 0) {
                    throw new IllegalArgumentException("You must pass both --x and --y when encoding NVSG");
                }
                convertFromPng(input, output, imageCount, x, y);
            } else {
                System.out.println("Unknown mode: " + mode);
                printUsage();
            }
        } catch (Exception e) {
            System.err.println("Error: " + e.getMessage());
            e.printStackTrace();
        }
    }

    private static void printUsage() {
        System.out.println("Usage:");
        System.out.println("  java NVSGTool --decode input.nvsg output.png");
        System.out.println("  java NVSGTool --encode input.png output.nvsg --x value --y value [--image_count value]");
    }

    // =========================
    // NVSG -> PNG
    // =========================
    public static void convertToPng(File input, File output) throws Exception {
        try (DataInputStream dis = new DataInputStream(new BufferedInputStream(new FileInputStream(input)))) {
            byte[] magic = new byte[4];
            dis.readFully(magic);
            if (!new String(magic).equals("hzc1"))
                throw new IOException("Not a valid NVSG file");

            int uncompressedSize = readU32(dis);
            int headerSize = readU32(dis);

            dis.readFully(magic);
            if (!new String(magic).equals("NVSG"))
                throw new IOException("Missing NVSG header");

            int always256 = readU16(dis);
            int format = readU16(dis);
            int width = readU16(dis);
            int height = readU16(dis);
            int x = readU16(dis);
            int y = readU16(dis);
            int unk1 = readU16(dis);
            int unk2 = readU16(dis);
            int imageCount = readU32(dis);
            int unk3 = readU32(dis);
            int unk4 = readU32(dis);

            byte[] compressed = dis.readAllBytes();
            byte[] data = decompressZlib(compressed);

            BufferedImage img;
            switch (format) {
                case 0: img = makeImage(width, height, data, false); break;
                case 1: img = makeImage(width, height, data, true); break;
                case 2:
                    height *= imageCount;
                    img = makeImage(width, height, data, true);
                    break;
                case 3: img = makeGrayImage(width, height, data); break;
                default: throw new IOException("Unsupported format: " + format);
            }

            ImageIO.write(img, "png", output);

            // Print all info for logging
            System.out.printf(
                "Decoded %s -> %s (x=%d, y=%d, count=%d, width=%d, height=%d, format=%d)%n",
                input.getName(), output.getName(),
                x, y, imageCount, width, height, format
            );
        }
    }

    // =========================
    // PNG -> NVSG
    // =========================
    public static void convertFromPng(File input, File output, int imageCount, int x, int y) throws Exception {
        BufferedImage img = ImageIO.read(input);
        if (img == null) throw new IOException("Cannot read PNG");

        int width = img.getWidth();
        int height = img.getHeight();

        int format;
        boolean hasAlpha = img.getColorModel().hasAlpha();
        if (imageCount > 1) {
            format = 2;
            height /= imageCount;
        } else if (hasAlpha) {
            format = 1;
        } else {
            format = 0;
        }

        byte[] data = bytesFromImageForNVSG(img, (format == 1 || format == 2));
        byte[] compressed = compressZlib(data);

        try (DataOutputStream dos = new DataOutputStream(new BufferedOutputStream(new FileOutputStream(output)))) {
            dos.write(HZC1_MAGIC);
            writeU32(dos, data.length);
            writeU32(dos, 0x20);
            dos.write(NVSG_MAGIC);
            writeU16(dos, 256);
            writeU16(dos, format);
            writeU16(dos, width);
            writeU16(dos, height);
            writeU16(dos, x);
            writeU16(dos, y);
            writeU16(dos, 0);
            writeU16(dos, 0);
            writeU32(dos, imageCount);
            writeU32(dos, 0);
            writeU32(dos, 0);
            dos.write(compressed);
        }

        System.out.printf("Encoded %s -> %s (x=%d, y=%d, count=%d, width=%d, height=%d, format=%d)%n",
                input.getName(), output.getName(), x, y, imageCount, width, height, format);
    }

    // ==== Helpers (binary + zlib + images) ====

    private static int readU16(DataInputStream dis) throws IOException {
        return Short.toUnsignedInt(Short.reverseBytes(dis.readShort()));
    }
    private static int readU32(DataInputStream dis) throws IOException {
        return Integer.reverseBytes(dis.readInt());
    }
    private static void writeU16(DataOutputStream dos, int value) throws IOException {
        dos.write(ByteBuffer.allocate(2).order(ByteOrder.LITTLE_ENDIAN).putShort((short)value).array());
    }
    private static void writeU32(DataOutputStream dos, int value) throws IOException {
        dos.write(ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(value).array());
    }

    private static byte[] decompressZlib(byte[] compressed) throws Exception {
        Inflater inflater = new Inflater();
        inflater.setInput(compressed);
        ByteArrayOutputStream baos = new ByteArrayOutputStream();
        byte[] buf = new byte[4096];
        while (!inflater.finished()) {
            int len = inflater.inflate(buf);
            if (len == 0 && inflater.needsInput()) break;
            baos.write(buf, 0, len);
        }
        inflater.end();
        return baos.toByteArray();
    }
    private static byte[] compressZlib(byte[] data) throws Exception {
        Deflater deflater = new Deflater(Deflater.BEST_COMPRESSION);
        deflater.setInput(data);
        deflater.finish();
        ByteArrayOutputStream baos = new ByteArrayOutputStream();
        byte[] buf = new byte[4096];
        while (!deflater.finished()) {
            int len = deflater.deflate(buf);
            baos.write(buf, 0, len);
        }
        deflater.end();
        return baos.toByteArray();
    }

    private static BufferedImage makeImage(int width, int height, byte[] data, boolean alpha) {
        if (alpha) {
            BufferedImage img = new BufferedImage(width, height, BufferedImage.TYPE_4BYTE_ABGR);
            byte[] dest = ((DataBufferByte) img.getRaster().getDataBuffer()).getData();
            for (int i = 0; i < width * height; i++) {
                int s = i * 4, d = i * 4;
                dest[d] = data[s + 3];     // A
                dest[d + 1] = data[s];     // B
                dest[d + 2] = data[s + 1]; // G
                dest[d + 3] = data[s + 2]; // R
            }
            return img;
        } else {
            BufferedImage img = new BufferedImage(width, height, BufferedImage.TYPE_3BYTE_BGR);
            byte[] dest = ((DataBufferByte) img.getRaster().getDataBuffer()).getData();
            System.arraycopy(data, 0, dest, 0, Math.min(data.length, dest.length));
            return img;
        }
    }

    private static BufferedImage makeGrayImage(int width, int height, byte[] data) {
        BufferedImage img = new BufferedImage(width, height, BufferedImage.TYPE_BYTE_GRAY);
        byte[] dest = ((DataBufferByte) img.getRaster().getDataBuffer()).getData();
        System.arraycopy(data, 0, dest, 0, Math.min(data.length, dest.length));
        return img;
    }

    private static byte[] bytesFromImageForNVSG(BufferedImage img, boolean alpha) {
        byte[] src = ((DataBufferByte) img.getRaster().getDataBuffer()).getData();
        int w = img.getWidth(), h = img.getHeight();
        byte[] out;
        if (alpha) {
            out = new byte[w * h * 4];
            for (int i = 0; i < w * h; i++) {
                int s = i * 4, d = i * 4;
                out[d] = src[s + 1];     // B
                out[d + 1] = src[s + 2]; // G
                out[d + 2] = src[s + 3]; // R
                out[d + 3] = src[s];     // A
            }
        } else {
            out = new byte[w * h * 3];
            System.arraycopy(src, 0, out, 0, Math.min(src.length, out.length));
        }
        return out;
    }
}
