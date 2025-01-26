from enum import Enum


EC_CURVE_DEFAULT = 'SECP384R1'


class DigestAlgorithm(str, Enum):
    SHA1 = 'SHA1'
    SHA224 = 'SHA224'
    SHA256 = 'SHA256'
    SHA384 = 'SHA384'
    SHA512 = 'SHA512'


class EC_CURVES(str, Enum):
    SECP256R1 = 'SECP256R1'
    SECP384R1 = 'SECP384R1'
    SECP521R1 = 'SECP521R1'
    ed25519 = 'ed25519'


# We would want to hard code EKU OIDs here because if cryptography version is bumped, that will mean there could
# be dynamic update to API which we don't want
class EkuOID(str, Enum):
    ANY_EXTENDED_KEY_USAGE = 'ANY_EXTENDED_KEY_USAGE'
    CERTIFICATE_TRANSPARENCY = 'CERTIFICATE_TRANSPARENCY'
    CLIENT_AUTH = 'CLIENT_AUTH'
    CODE_SIGNING = 'CODE_SIGNING'
    EMAIL_PROTECTION = 'EMAIL_PROTECTION'
    IPSEC_IKE = 'IPSEC_IKE'
    KERBEROS_PKINIT_KDC = 'KERBEROS_PKINIT_KDC'
    OCSP_SIGNING = 'OCSP_SIGNING'
    SERVER_AUTH = 'SERVER_AUTH'
    SMARTCARD_LOGON = 'SMARTCARD_LOGON'
    TIME_STAMPING = 'TIME_STAMPING'
