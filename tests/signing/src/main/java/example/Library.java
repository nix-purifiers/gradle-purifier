package example;

/** A library with UTF-8 documentation (café) whose release needs a signing key. */
public final class Library {
    private Library() {}

    /** Returns the fixture's observable value. */
    public static String value() { return "signed-library"; }
}
