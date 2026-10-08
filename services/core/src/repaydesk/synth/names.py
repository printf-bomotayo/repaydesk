"""Name and place lists for synthetic borrowers. Invented combinations, not real customers."""

from repaydesk.domain import Branch, Product

FIRST_NAMES = {
    "yoruba": [
        "Adebayo", "Adewale", "Funmilayo", "Folasade", "Olumide", "Oluwaseun", "Temitope", "Bukola",
        "Kehinde", "Taiwo", "Yetunde", "Babajide", "Abiodun", "Omolara", "Tunde", "Basirat", "Rasheed",
        "Kafayat", "Iyabo", "Segun", "Morenike", "Damilola", "Ayodele", "Modupe",
    ],
    "igbo": [
        "Chika", "Chioma", "Ngozi", "Emeka", "Obinna", "Chinedu", "Ifeoma", "Nkechi", "Uchenna",
        "Chidi", "Adaeze", "Ebuka", "Amaka", "Ikenna", "Nneka", "Kelechi", "Chiamaka", "Obiageli",
    ],
    "hausa": [
        "Musa", "Aisha", "Ibrahim", "Fatima", "Abubakar", "Zainab", "Usman", "Hauwa", "Sani",
        "Hadiza", "Yusuf", "Halima", "Aminu", "Maryam", "Bello", "Rabi",
    ],
    "other": [
        "Efe", "Ese", "Ovie", "Tamuno", "Ibiere", "Ekaette", "Imaobong", "Blessing", "Peace", "Godwin",
        "Patience", "Friday", "Sunday", "Joy", "Victor", "Grace",
    ],
}

LAST_NAMES = {
    "yoruba": [
        "Adeyemi", "Ogunleye", "Balogun", "Akinola", "Okonjo", "Adebisi", "Olatunji", "Bakare",
        "Oyelaran", "Fashola", "Adeleke", "Salami", "Ojo", "Lawal", "Afolabi", "Oladipo", "Ajayi",
    ],
    "igbo": [
        "Okafor", "Nwosu", "Eze", "Okeke", "Obi", "Nwachukwu", "Onyekachi", "Chukwu", "Okonkwo",
        "Ibe", "Anyanwu", "Udeh", "Agu", "Nnaji", "Ezeh",
    ],
    "hausa": [
        "Abdullahi", "Mohammed", "Bello", "Garba", "Danjuma", "Lawan", "Sule", "Tanko", "Yakubu",
        "Haruna", "Shehu", "Inuwa",
    ],
    "other": [
        "Edet", "Okon", "Efiong", "Briggs", "Tamuno", "Oghene", "Etim", "Akpan", "Ikpe", "Douglas",
    ],
}

# Where a branch is, which names are common there.
BRANCH_MIX = {
    "south_west": {"yoruba": 0.7, "igbo": 0.15, "hausa": 0.05, "other": 0.1},
    "south_east": {"igbo": 0.8, "yoruba": 0.05, "other": 0.15},
    "south_south": {"other": 0.6, "igbo": 0.25, "yoruba": 0.15},
    "north": {"hausa": 0.75, "yoruba": 0.1, "igbo": 0.1, "other": 0.05},
    "fct": {"hausa": 0.35, "yoruba": 0.25, "igbo": 0.25, "other": 0.15},
}

BRANCHES = [
    (Branch("IKJ", "Ikeja", "Lagos"), "south_west"),
    (Branch("OSH", "Oshodi", "Lagos"), "south_west"),
    (Branch("MSH", "Mushin", "Lagos"), "south_west"),
    (Branch("IBD", "Ibadan Dugbe", "Oyo"), "south_west"),
    (Branch("ABK", "Abeokuta", "Ogun"), "south_west"),
    (Branch("ONT", "Onitsha Main Market", "Anambra"), "south_east"),
    (Branch("PHC", "Port Harcourt Mile 1", "Rivers"), "south_south"),
    (Branch("KAN", "Kano Kantin Kwari", "Kano"), "north"),
    (Branch("WSE", "Abuja Wuse", "FCT"), "fct"),
]

PRODUCTS = [
    Product("QUICK", "Quick loan, 30 days", installments=1, monthly_flat_rate=0.05),
    Product("SME", "SME working capital, 6 months", installments=6, monthly_flat_rate=0.035),
]

# Trading names. {first} is filled with the borrower's first name.
BUSINESS_PATTERNS = [
    "Mama {first} Provisions", "Iya {first} Foods", "{first} & Sons Enterprises", "{first} Fashion House",
    "God's Grace Ventures", "Blessed Hands Tailoring", "{first} Phones & Accessories", "{first} Pharmacy",
    "Divine Favour Stores", "{first} Cold Room", "Mr {first} Electricals", "{first} Hair Studio",
    "Alhaji {first} Grains", "Hajiya {first} Textiles", "{first} Building Materials", "Success Motors Spares",
    "{first} Kitchen", "New Dawn Supermarket", "{first} Poultry Farm", "Ever Green Pure Water",
]

GROUP_NAMES = [
    "Ifeoluwa Traders", "Ndi Ahia Women", "Alheri Market Women", "Oke-Ado Progressive", "Unity Sellers",
    "Ireti Cooperative", "Nkemakolam Union", "Hadin Kai Traders", "Agbalaya Wives", "Ebenezer Market Group",
]

# Fictional collection banks and providers; their statement formats are generic.
COLLECTION_ACCOUNTS = {
    "bank_csv": ("Northgate Bank", "1023456789"),
    "bank_mt940": ("Coastline Bank", "2034567891"),
    "virtual_account": ("PayRail (virtual accounts)", "PAYRAIL-SETTLE"),
    "pos": ("TermPay (POS settlement)", "TERMPAY-SETTLE"),
    "branch_cash": ("Branch cash", "BRANCH-CASH"),
}

SENDER_BANKS = ["GTB", "ACCESS", "ZENITH", "UBA", "FIRSTBANK", "OPAY", "MONIEPOINT", "PALMPAY", "KUDA", "FIDELITY"]
