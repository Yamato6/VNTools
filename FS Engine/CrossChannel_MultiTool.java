import java.io.*;
import java.nio.charset.StandardCharsets;

public class CrossChannel_MultiTool {

    public static void main(String[] args) {
        if (args.length < 1) {
            usage();
            return;
        }

        String cmd = args[0].toLowerCase();

        try {
            switch (cmd) {
                case "extract":
                    if (args.length != 3) {
                        usage();
                        return;
                    }
                    extract(args[1], args[2]);
                    break;

                case "pack":
                    if (args.length != 4) {
                        usage();
                        return;
                    }
                    pack(args[1], args[2], args[3]);
                    break;

                default:
                    usage();
            }
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    private static void usage() {
        System.out.println("Uso:");
        System.out.println("  java CrossChannel_MultiTool extract <archivo.PD> <directorio_salida>");
        System.out.println("  java CrossChannel_MultiTool pack <directorio_entrada> <archivo_salida.PD> <PackOnly|PackPlus>");
    }

    // ----------------------------------------
    // EXTRAER ARCHIVO .PD
    // ----------------------------------------
    private static void extract(String pdFile, String outDir) throws IOException {
        try (RandomAccessFile arcfile = new RandomAccessFile(pdFile, "r")) {
            // Leer modo
            byte[] modeBytes = new byte[8];
            arcfile.readFully(modeBytes);
            String mode = new String(modeBytes, StandardCharsets.ISO_8859_1).trim();

            if (!mode.equals("PackOnly") && !mode.equals("PackPlus")) {
                throw new IOException("Formato desconocido: " + mode);
            }
            int xorbyte = mode.equals("PackOnly") ? 0 : 0xFF;

            CrossUtils.assertZeroes(arcfile, 56, CrossUtils.ERROR_ABORT);
            long fileCount = CrossUtils.readUnsigned(arcfile, 8, CrossUtils.LITTLE_ENDIAN);

            System.out.println("Extrayendo " + fileCount + " archivos desde " + pdFile + " (" + mode + ")...");

            for (int i = 0; i < fileCount; i++) {
                byte[] nameBytes = new byte[128];
                arcfile.readFully(nameBytes);
                String filename = new String(nameBytes, StandardCharsets.ISO_8859_1).trim();
                if (filename.indexOf(0) >= 0) filename = filename.substring(0, filename.indexOf(0));

                long offset = CrossUtils.readUnsigned(arcfile, 8, CrossUtils.LITTLE_ENDIAN);
                long length = CrossUtils.readUnsigned(arcfile, 8, CrossUtils.LITTLE_ENDIAN);

                long pos = arcfile.getFilePointer();

                // Extraer archivo
                arcfile.seek(offset);
                byte[] buffer = new byte[(int) length];
                arcfile.readFully(buffer);

                if (xorbyte != 0) {
                    for (int j = 0; j < buffer.length; j++) {
                        buffer[j] ^= (byte) xorbyte;
                    }
                }

                File outFile = new File(outDir, filename);
                outFile.getParentFile().mkdirs();
                try (FileOutputStream fos = new FileOutputStream(outFile)) {
                    fos.write(buffer);
                }

                arcfile.seek(pos);
                System.out.println(" -> " + filename + " (" + length + " bytes)");
            }

            System.out.println("Extraccion completada en: " + outDir);
        }
    }

    // ----------------------------------------
    // CREAR ARCHIVO .PD
    // ----------------------------------------
    private static void pack(String inDir, String outFile, String mode) throws IOException {
        if (!mode.equals("PackOnly") && !mode.equals("PackPlus")) {
            throw new IOException("Modo inválido: " + mode);
        }
        int xorbyte = mode.equals("PackOnly") ? 0 : 0xFF;

        File dir = new File(inDir);
        File[] files = dir.listFiles(File::isFile);
        if (files == null) throw new IOException("Directorio vacío o inválido");

        try (RandomAccessFile arcfile = new RandomAccessFile(outFile, "rw")) {
            arcfile.setLength(0);

            // Header
            arcfile.write(mode.getBytes(StandardCharsets.ISO_8859_1));
            CrossUtils.writeZeroes(arcfile, 56);
            CrossUtils.writeUnsigned(arcfile, files.length, 8, CrossUtils.LITTLE_ENDIAN);

            // Reservar espacio para directorio
            long directoryStart = arcfile.getFilePointer();
            long directorySize = (128 + 8 + 8) * files.length;
            CrossUtils.writeZeroes(arcfile, (int) directorySize);

            long[] offsets = new long[files.length];
            long[] lengths = new long[files.length];

            // Escribir archivos
            for (int i = 0; i < files.length; i++) {
                File f = files[i];
                offsets[i] = arcfile.getFilePointer();

                byte[] buffer = new byte[(int) f.length()];
                try (FileInputStream fis = new FileInputStream(f)) {
                    fis.read(buffer);
                }

                if (xorbyte != 0) {
                    for (int j = 0; j < buffer.length; j++) {
                        buffer[j] ^= (byte) xorbyte;
                    }
                }

                arcfile.write(buffer);
                lengths[i] = buffer.length;

                System.out.println(" <- " + f.getName() + " (" + buffer.length + " bytes)");
            }

            // Rellenar directorio
            arcfile.seek(directoryStart);
            for (int i = 0; i < files.length; i++) {
                File f = files[i];
                byte[] nameBytes = f.getName().getBytes(StandardCharsets.ISO_8859_1);
                if (nameBytes.length > 128) throw new IOException("Nombre de archivo demasiado largo: " + f.getName());

                byte[] padded = new byte[128];
                System.arraycopy(nameBytes, 0, padded, 0, nameBytes.length);
                arcfile.write(padded);

                CrossUtils.writeUnsigned(arcfile, offsets[i], 8, CrossUtils.LITTLE_ENDIAN);
                CrossUtils.writeUnsigned(arcfile, lengths[i], 8, CrossUtils.LITTLE_ENDIAN);
            }

            System.out.println("Paquete creado: " + outFile + " (" + mode + ")");
        }
    }
}

