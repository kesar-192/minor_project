from phe_logic import encrypt, add, decrypt, ciphertext_to_dict, dict_to_ciphertext

encrypted_a = encrypt(100)
encrypted_b = encrypt(200)

data_a = ciphertext_to_dict(encrypted_a)
data_b = ciphertext_to_dict(encrypted_b)

restored_a = dict_to_ciphertext(data_a)
restored_b = dict_to_ciphertext(data_b)

encrypted_total = add([restored_a, restored_b])

result = decrypt(encrypted_total, "user123")

print("Encrypted addition result:", result)

assert result == 300, "Test failed!"
print("Test passed!")
