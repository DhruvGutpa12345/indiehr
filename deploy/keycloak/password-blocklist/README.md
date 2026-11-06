# Common-password block list (P1-T3)

`common-passwords.txt` is used by the Keycloak password policy `passwordBlacklist(common-passwords.txt)`. It lists 46,146 common passwords that are 12 to 128 characters long, lower-cased, ASCII only, de-duplicated and kept in frequency order. Shorter passwords are already refused by `length(12)`.

**Source:** `Passwords/Common-Credentials/xato-net-10-million-passwords-1000000.txt` from [SecLists](https://github.com/danielmiessler/SecLists) at commit `2f31b22e1f9963ec2fd2b12606c00f0fb04910f1`.

**Licence:** MIT, Copyright (c) 2018 Daniel Miessler.

**How it was built:**

```bash
awk 'length>=12 && length<=128' xato-net-10-million-passwords-1000000.txt | tr 'A-Z' 'a-z' \
  | LC_ALL=C grep -v '[^ -~]' | awk '!s[$0]++' > common-passwords.txt
```

The SecLists top-10k list alone holds only 10 passwords of 12 or more characters, so it would add almost nothing on top of the length rule.
