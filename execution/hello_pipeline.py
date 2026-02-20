def transform_name(name: str) -> str:
    """
    Normalize a name and return a hello-formatted string.
    - Strip surrounding whitespace
    - Convert to lowercase
    - Return format: "hello_<normalized_name>"
    """
    normalized = name.strip().lower()
    return f"hello_{normalized}"


if __name__ == "__main__":
    print(transform_name("  Intern  "))
