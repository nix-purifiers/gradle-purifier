// SPDX-License-Identifier: MIT

import java.io.File;

/**
 * Where the Gradle wrapper of a build looks for its distribution: `gradle-purifier prefetch` puts
 * the locked distribution there, so ./gradlew downloads nothing. It asks the wrapper's own code
 * (PathAssembler, from the gradle-wrapper-shared jar of the distribution), so the paths are the
 * ones the wrapper uses.
 *
 * <p>The implementation package compiles this helper for Java 8. Reflective calls retain the
 * native wrapper API without requiring its classes at compilation time or a source compiler
 * in the installed project runtime.
 *
 * <p>Usage: {@code java -cp <helper>:<gradle-wrapper-shared jar> WrapperDistribution <Gradle
 * user home> <build root>}. It prints the distribution directory and its zip path.
 */
public final class WrapperDistribution {

  public static void main(String[] args) throws ReflectiveOperationException {
    File gradleUserHome = new File(args[0]);
    File root = new File(args[1]);
    Class<?> executorClass = Class.forName("org.gradle.wrapper.WrapperExecutor");
    Object executor = executorClass.getMethod("forWrapperPropertiesFile", File.class)
        .invoke(null, new File(root, "gradle/wrapper/gradle-wrapper.properties"));
    Object configuration = executorClass.getMethod("getConfiguration").invoke(executor);
    Class<?> configurationClass = Class.forName("org.gradle.wrapper.WrapperConfiguration");
    Class<?> assemblerClass = Class.forName("org.gradle.wrapper.PathAssembler");
    Object assembler = assemblerClass.getConstructor(File.class, File.class)
        .newInstance(gradleUserHome, root);
    Object distribution = assemblerClass.getMethod("getDistribution", configurationClass)
        .invoke(assembler, configuration);
    File directory = (File) distribution.getClass().getMethod("getDistributionDir").invoke(distribution);
    File zip = (File) distribution.getClass().getMethod("getZipFile").invoke(distribution);
    System.out.println(directory.getAbsolutePath());
    System.out.println(zip.getAbsolutePath());
  }
}
