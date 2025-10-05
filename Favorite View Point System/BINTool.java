import java.io.*;
import java.nio.charset.Charset;
import java.util.*;
import java.util.regex.*;

public class BINTool {

    // Calcula el tamaño de la tabla de archivos
    public static int getTableSize(int fileCount) {
        return fileCount * 12;
    }

    // Convierte un nombre codificado a ruta con índice
    public static String nameToPath(int i, byte[] name) {
        return String.format("%04d_%s", i, new String(name, Charset.forName("Shift_JIS")));
    }

    // Convierte una ruta a nombre SJIS (quitando prefijo numérico)
    public static byte[] pathToName(String path) {
        String baseName = new File(path).getName();
        String cleanName = baseName.replaceFirst("^\\d+_", "");
        return cleanName.getBytes(Charset.forName("Shift_JIS"));
    }

    // Extrae archivos desde un .bin a una carpeta
    public static void extract(String filePath, String targetFolder) throws IOException {
        File outDir = new File(targetFolder);
        if (!outDir.exists()) outDir.mkdirs();

        try (FVPUtils file = new FVPUtils(filePath, "r")) {
            long fileCount = file.readU32LE();
            long fileNamesSize = file.readU32LE();
            long fileNamesStart = file.tell() + getTableSize((int) fileCount);

            for (int i = 0; i < fileCount; i++) {
                long fileNameOffset = file.readU32LE();
                long offset = file.readU32LE();
                long size = file.readU32LE();

                // Guarda la posición actual
                long oldPos = file.tell();

                // Lee el nombre del archivo
                file.seek(fileNamesStart + fileNameOffset);
                byte[] nameBytes = file.readUntilZero();
                String name = nameToPath(i, nameBytes);

                // Lee el contenido del archivo
                if (size < 0 || size > Integer.MAX_VALUE) {
                    throw new IOException("Invalid file size: " + size);
                }
                file.seek(offset);
                byte[] content = file.readBytes((int) size);
                file.seek(oldPos);

                // Escribe el archivo
                File targetFile = new File(outDir, name);
                System.out.println("→ Saving to " + targetFile.getAbsolutePath());
                try (FileOutputStream out = new FileOutputStream(targetFile)) {
                    out.write(content);
                }
            }
        }
        System.out.println("\n✔ Extraction completed successfully.");
    }

    // Empaqueta archivos de una carpeta en un .bin
    public static void pack(String sourceFolder, String filePath) throws IOException {
        File dir = new File(sourceFolder);
        File[] files = dir.listFiles(File::isFile);
        if (files == null) return;
        Arrays.sort(files);

        List<byte[]> names = new ArrayList<>();
        for (File f : files) names.add(pathToName(f.getAbsolutePath()));

        try (FVPUtils file = new FVPUtils(filePath, "rw")) {
            file.writeU32LE(names.size());
            file.writeU32LE(names.stream().mapToInt(n -> n.length + 1).sum());
            file.write(new byte[getTableSize(names.size())]);

            long fileNamesStart = file.tell();
            System.out.println("File names start offset: " + fileNamesStart);

            List<Map<String, Long>> table = new ArrayList<>();
            for (byte[] name : names) {
                Map<String, Long> entry = new HashMap<>();
                entry.put("name_offset", file.tell() - fileNamesStart);
                file.write(name);
                file.writeByte(0);
                table.add(entry);
            }

            // Escribir archivos binarios
            for (int i = 0; i < files.length; i++) {
                File source = files[i];
                System.out.println("→ Writing " + source.getName());
                Map<String, Long> entry = table.get(i);
                entry.put("file_offset", file.tell());

                try (FileInputStream in = new FileInputStream(source)) {
                    in.transferTo(fileOutputStream(file));
                }

                entry.put("file_size", file.tell() - entry.get("file_offset"));
            }

            // Escribir tabla al inicio
            file.seek(8);
            for (Map<String, Long> entry : table) {
                file.writeU32LE(entry.get("name_offset"));
                file.writeU32LE(entry.get("file_offset"));
                file.writeU32LE(entry.get("file_size"));
            }
        }

        System.out.println("\n✔ Packing completed successfully.");
    }

    // Convierte FVPUtils a OutputStream para poder usar transferTo()
    private static OutputStream fileOutputStream(FVPUtils fvp) {
        return new OutputStream() {
            @Override
            public void write(int b) throws IOException {
                fvp.writeByte(b);
            }

            @Override
            public void write(byte[] b) throws IOException {
                fvp.write(b);
            }

            @Override
            public void write(byte[] b, int off, int len) throws IOException {
                byte[] slice = Arrays.copyOfRange(b, off, off + len);
                fvp.write(slice);
            }
        };
    }

    // Main CLI
    public static void main(String[] args) throws IOException {
        if (args.length == 3) {
            switch (args[0]) {
                case "-d" -> extract(args[1], args[2]);
                case "-c" -> pack(args[1], args[2]);
                default -> printUsage();
            }
            return;
        }
        printUsage();
    }

    private static void printUsage() {
        System.out.println("Usage:");
        System.out.println("  java BINTool -d input.bin folder");
        System.out.println("  java BINTool -c folder output.bin\n");
        System.out.println("Options:");
        System.out.println("  -d   Extracts files from a BIN archive into a folder");
        System.out.println("  -c   Packs a folder into a BIN archive for the game\n");
        System.out.println("Note: The folder must contain only files extracted from the original archive.");
    }
}
