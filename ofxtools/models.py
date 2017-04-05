# vim: set fileencoding=utf-8
"""
Python object model for fundamental data aggregates such as transactions,
balances, and securities.
"""
# stdlib imports
import xml.etree.ElementTree as ET

# local imports
import ofxtools
from ofxtools.Types import (
    Element,
    Bool,
    String,
    OneOf,
    Integer,
    Decimal,
    DateTime,
    NagString,
)
from ofxtools.lib import LANG_CODES, CURRENCY_CODES, COUNTRY_CODES


# Enums used in aggregate validation
INV401KSOURCES = ('PRETAX', 'AFTERTAX', 'MATCH', 'PROFITSHARING',
                    'ROLLOVER', 'OTHERVEST', 'OTHERNONVEST')
ACCTTYPES = ('CHECKING', 'SAVINGS', 'MONEYMRKT', 'CREDITLINE')
INVSUBACCTS = ('CASH', 'MARGIN', 'SHORT', 'OTHER')
BUYTYPES = ('BUY', 'BUYTOCOVER')
SELLTYPES = ('SELL', 'SELLSHORT')
INCOMETYPES = ('CGLONG', 'CGSHORT', 'DIV', 'INTEREST', 'MISC')
ASSETCLASSES = ('DOMESTICBOND', 'INTLBOND', 'LARGESTOCK', 'SMALLSTOCK',
                'INTLSTOCK', 'MONEYMRKT', 'OTHER')


class Aggregate(object):
    """
    Base class for Python representation of OFX 'aggregate', i.e. SGML parent
    node that contains no data.

    Initialize with an instance of ofx.Parser.Element.

    This class represents fundamental data aggregates such as transactions,
    balances, and securities.  Subaggregates have been flattened so that
    data-bearing Elements are directly accessed as attributes of the
    containing Aggregate.

    Aggregates are grouped into higher-order containers such as lists
    and statements.  Although such higher-order containers are 'aggregates'
    per the OFX specification, they are represented here by their own Python
    classes other than Aggregate.
    """
    def __init__(self, **kwargs):
        for name, element in self.elements.items():
            value = kwargs.pop(name, None)
            try:
                setattr(self, name, value)
            except ValueError as e:
                raise ValueError("Can't create %s.%s: %s" 
                                 % (self.__class__.__name__, name, e.args[0]),
                                )
        if kwargs:
            raise ValueError("Undefined element(s) for '%s': %s"
                            % (self.__class__.__name__, kwargs.keys())
                            )

    @property
    def elements(self):
        """ """
        d = {}
        for m in self.__class__.__mro__:
            d.update({k: v for k,v in m.__dict__.items() \
                                    if isinstance(v, Element)})
        return d

    @classmethod
    def from_etree(cls, elem):
        """
        Look up the Aggregate subclass for a given ofx.Parser.Element and
        feed it the Element to instantiate the Aggregate instance.
        """
        cls._groom(elem)
        SubClass = globals()[elem.tag]
        attrs, subaggs = SubClass._preflatten(elem)
        attributes = cls._flatten(elem)
        instance = SubClass(**attributes)
        cls._postflatten(instance, attrs, subaggs)
        return instance

    @staticmethod
    def _groom(elem):
        """ """
        # Rename all Elements tagged YIELD (reserved Python keyword) to YLD
        yld = elem.find('./YIELD')
        if yld is not None:
            yld.tag = 'YLD'

        # Throw an error for Elements containing sub-Elements that are
        # mutually exclusive per the OFX spec, and which will cause
        # problems for _flatten()
        for dual_relationships in [
                ["CCACCTTO", "BANKACCTTO"],
                ["NAME", "PAYEE"],
                ["CURRENCY", "ORIGCURRENCY"],
        ]:
            if (elem.find(dual_relationships[0]) is not None and
                elem.find(dual_relationships[1]) is not None):
                raise ValueError(
                    "<%s> may not contain both <%s> and <%s>" %
                    (elem.tag, dual_relationships[0],
                     dual_relationships[1]))

    @staticmethod
    def _preflatten(elem):
        """ Extend in subclass """
        return {}, {}

    @classmethod
    def _flatten(cls, element):
        """
        Recurse through aggregate and flatten; return an un-nested dict.

        This method will blow up if the aggregate contains LISTs, or if it
        contains multiple subaggregates whose namespaces will collide when
        flattened (e.g. BALAMT/DTASOF elements in LEDGERBAL and AVAILBAL).
        Remove all such hair from any element before passing it in here.
        """
        aggs = {}
        leaves = {}
        for child in element:
            tag = child.tag
            data = child.text or ''
            data = data.strip()
            if data:
                # it's a data-bearing leaf element.
                assert tag not in leaves
                # Silently drop all private tags (e.g. <INTU.XXXX>
                if '.' not in tag:
                    leaves[tag.lower()] = data
            else:
                # it's an aggregate.
                assert tag not in aggs
                aggs.update(cls._flatten(child))
        # Double-check no key collisions as we flatten aggregates & leaves
        for key in aggs.keys():
            assert key not in leaves
        leaves.update(aggs)

        return leaves

    @staticmethod
    def _postflatten(instance, attrs, subaggs):
        """ """
        for attr, value in attrs.items():
            setattr(instance, attr, value)
        for tag, elem in subaggs.items():
            if isinstance(elem, ET.Element):
                setattr(instance, tag.lower(), Aggregate.from_etree(elem))
            elif isinstance(elem, (list, tuple)):
                lst = [Aggregate.from_etree(elem) for e in elem]
                setattr(instance, tag.lower(), lst)
            else:
                msg = "'{}' must be type {} or {}, not {}".format(
                    tag, 'ElementTree.Element', 'list', type(elem)
                )
                raise ValueError(msg)

    def __repr__(self):
        return '<%s %s>' % (self.__class__.__name__, ' '.join(['%s=%r' % (attr, str(getattr(self, attr))) for attr in self.elements.keys() if getattr(self, attr) is not None]))


class FI(Aggregate):
    """
    FI aggregates are optional in SONRQ/SONRS; not all firms use them.
    """
    org = String(32)
    fid = String(32)


class STATUS(Aggregate):
    code = Integer(6, required=True)
    severity = OneOf('INFO', 'WARN', 'ERROR', required=True)
    message = String(255)


class SONRS(FI, STATUS):
    dtserver = DateTime(required=True)
    userkey = String(64)
    tskeyexpire = DateTime()
    language = OneOf(*LANG_CODES)
    dtprofup = DateTime()
    dtacctup = DateTime()
    sesscookie = String(1000)
    accesskey = String(1000)


class CURRENCY(Aggregate):
    cursym = OneOf(*CURRENCY_CODES)
    currate = Decimal(8)


class ORIGCURRENCY(CURRENCY):
    curtype = OneOf('CURRENCY', 'ORIGCURRENCY')

    @staticmethod
    def _preflatten(elem):
        """
        See OFX spec section 5.2 for currency handling conventions.
        Flattening the currency definition leaves only the CURRATE/CURSYM
        elements, leaving no indication of whether these were sourced from
        a CURRENCY aggregate or ORIGCURRENCY.  Since this distinction is
        important to interpreting transactions in foreign correncies, we
        preserve this information by adding a nonstandard curtype element.
        """
        attr, subagg = super(ORIGCURRENCY, ORIGCURRENCY)._preflatten(elem)

        curtype = elem.find('*/CURRENCY') or elem.find('*/ORIGCURRENCY')
        if curtype is not None:
            assert 'curtype' not in attr
            attr['curtype'] = curtype.text

        return attr, subagg


class ACCTFROM(Aggregate):
    acctid = String(22, required=True)


class BANKACCTFROM(ACCTFROM):
    bankid = String(9, required=True)
    branchid = String(22)
    accttype = OneOf(*ACCTTYPES,
                    required=True)
    acctkey = String(22)


class BANKACCTTO(BANKACCTFROM):
    pass


class CCACCTFROM(ACCTFROM):
    acctkey = String(22)


class CCACCTTO(CCACCTFROM):
    pass


class INVACCTFROM(ACCTFROM):
    brokerid = String(22, required=True)


# Balances
class LEDGERBAL(Aggregate):
    balamt = Decimal(required=True)
    dtasof = DateTime(required=True)


class AVAILBAL(Aggregate):
    balamt = Decimal(required=True)
    dtasof = DateTime(required=True)


class INVBAL(Aggregate):
    availcash = Decimal(required=True)
    marginbalance = Decimal(required=True)
    shortbalance = Decimal(required=True)
    buypower = Decimal()


class BAL(CURRENCY):
    name = String(32, required=True)
    desc = String(80, required=True)
    baltype = OneOf('DOLLAR', 'PERCENT', 'NUMBER', required=True)
    value = Decimal(required=True)
    dtasof = DateTime()


# Securities
class SECID(Aggregate):
    uniqueid = String(32, required=True)
    uniqueidtype = String(10, required=True)


class SECINFO(CURRENCY, SECID):
    # FIs abuse SECNAME/TICKER
    # Relaxing the length constraints from the OFX spec does little harm
    #secname = String(120, required=True)
    secname = NagString(120, required=True)
    #ticker = String(32)
    ticker = NagString(32)
    fiid = String(32)
    rating = String(10)
    unitprice = Decimal()
    dtasof = DateTime()
    memo = String(255)


class DEBTINFO(SECINFO):
    parvalue = Decimal(required=True)
    debttype = OneOf('COUPON', 'ZERO', required=True)
    debtclass = OneOf('TREASURY', 'MUNICIPAL', 'CORPORATE', 'OTHER')
    couponrt = Decimal(4)
    dtcoupon = DateTime()
    couponfreq = OneOf('MONTHLY', 'QUARTERLY', 'SEMIANNUAL', 'ANNUAL',
                       'OTHER')
    callprice = Decimal(4)
    yieldtocall = Decimal(4)
    dtcall = DateTime()
    calltype = OneOf('CALL', 'PUT', 'PREFUND', 'MATURITY')
    yieldtomat = Decimal(4)
    dtmat = DateTime()
    assetclass = OneOf(*ASSETCLASSES)
    fiassetclass = String(32)


class MFINFO(SECINFO):
    mftype = OneOf('OPENEND', 'CLOSEEND', 'OTHER')
    yld = Decimal(4)
    dtyieldasof = DateTime()

    mfassetclass = []
    fimfassetclass = []

    @staticmethod
    def _preflatten(elem):
        """
        Strip MFASSETCLASS/FIMFASSETCLASS - lists that will blow up _flatten()
        """
        # Do all XPath searches before removing nodes from the tree
        #   which seems to mess up the DOM in Python3 and throw an
        #   AttributeError on subsequent searches.
        attrs, subaggs = super(MFINFO, MFINFO)._preflatten(elem)

        mfassetclass = elem.find('./MFASSETCLASS')
        fimfassetclass = elem.find('./FIMFASSETCLASS')

        if mfassetclass is not None:
            subaggs['MFASSETCLASS'] = mfassetclass
            elem.remove(mfassetclass)
        if fimfassetclass is not None:
            subaggs['FIMFASSETCLASS'] = fimfassetclass
            elem.remove(fimfassetclass)

        return attrs, subaggs

class PORTION(Aggregate):
    assetclass = OneOf(*ASSETCLASSES, required=True)
    percent = Decimal(required=True)


class FIPORTION(Aggregate):
    fiassetclass = String(32, required=True)
    percent = Decimal(required=True)


class OPTINFO(SECINFO):
    opttype = OneOf('CALL', 'PUT', required=True)
    strikeprice = Decimal(required=True)
    dtexpire = DateTime(required=True)
    shperctrct = Integer(required=True)
    assetclass = OneOf(*ASSETCLASSES)
    fiassetclass = String(32)

    @staticmethod
    def _preflatten(elem):
        """
        Strip SECID of underlying so it doesn't overwrite SECID of option
        during _flatten()
        """
        # Do all XPath searches before removing nodes from the tree
        #   which seems to mess up the DOM in Python3 and throw an
        #   AttributeError on subsequent searches.
        attrs, subaggs = super(OPTINFO, OPTINFO)._preflatten(elem)

        secid = elem.find('./SECID')
        if secid is not None:
            # A <SECID> aggregate referring to the security underlying the
            # option is, in general, *not* going to be contained in <SECLIST>
            # (because you don't necessarily have a position in the underlying).
            # Since the <SECID> for the underlying only gives us fields for
            # (uniqueidtype, uniqueid) we can't really go ahead and use this
            # information to create a corresponding SECINFO instance (since we
            # lack information about the security subclass).  It's unclear that
            # the SECID of the underlying is really needed for anything, so we
            # disregard it.
            elem.remove(secid)

        return attrs, subaggs


class OTHERINFO(SECINFO):
    typedesc = String(32)
    assetclass = OneOf(*ASSETCLASSES)
    fiassetclass = String(32)


class STOCKINFO(SECINFO):
    stocktype = OneOf('COMMON', 'PREFERRED', 'CONVERTIBLE', 'OTHER')
    yld = Decimal(4)
    dtyieldasof = DateTime()
    typedesc = String(32)
    assetclass = OneOf(*ASSETCLASSES)
    fiassetclass = String(32)


# Transactions
class PAYEE(Aggregate):
    #name = String(32, required=True)
    name = NagString(32, required=True)
    addr1 = String(32, required=True)
    addr2 = String(32)
    addr3 = String(32)
    city = String(32, required=True)
    state = String(5, required=True)
    postalcode = String(11, required=True)
    country = OneOf(*COUNTRY_CODES)
    phone = String(32, required=True)


class TRAN(Aggregate):
    fitid = String(255, required=True)
    srvrtid = String(10)


class STMTTRN(TRAN, ORIGCURRENCY):
    trntype = OneOf('CREDIT', 'DEBIT', 'INT', 'DIV', 'FEE', 'SRVCHG',
                    'DEP', 'ATM', 'POS', 'XFER', 'CHECK', 'PAYMENT',
                    'CASH', 'DIRECTDEP', 'DIRECTDEBIT', 'REPEATPMT',
                    'OTHER', required=True)
    dtposted = DateTime(required=True)
    dtuser = DateTime()
    dtavail = DateTime()
    trnamt = Decimal(required=True)
    correctfitid = Decimal()
    correctaction = OneOf('REPLACE', 'DELETE')
    checknum = String(12)
    refnum = String(32)
    sic = Integer()
    payeeid = String(12)
    name = String(32)
    memo = String(255)
    inv401ksource = OneOf(*INV401KSOURCES)

    payee = None
    bankacctto = None
    ccacctto = None

    @staticmethod
    def _preflatten(elem):
        """ Handle CCACCTO/BANKACCTTO/PAYEE as 'sub-aggregates' """
        attrs, subaggs = super(STMTTRN, STMTTRN)._preflatten(elem)

        # Do all XPath searches before removing nodes from the tree
        #   which seems to mess up the DOM in Python3 and throw an
        #   AttributeError on subsequent searches.
        for tag in ["CCACCTTO", "BANKACCTTO", "PAYEE"]:
            ccacctto = elem.find(tag)
            if ccacctto is not None:
                elem.remove(ccacctto)
                subaggs[tag] = ccacctto

        return attrs, subaggs

class INVBANKTRAN(STMTTRN):
    subacctfund = OneOf(*INVSUBACCTS, required=True)


class INVTRAN(TRAN):
    dttrade = DateTime(required=True)
    dtsettle = DateTime()
    reversalfitid = String(255)
    memo = String(255)


class INVBUY(INVTRAN, SECID, ORIGCURRENCY):
    units = Decimal(required=True)
    unitprice = Decimal(4, required=True)
    markup = Decimal()
    commission = Decimal()
    taxes = Decimal()
    fees = Decimal()
    load = Decimal()
    total = Decimal(required=True)
    subacctsec = OneOf(*INVSUBACCTS, required=True)
    subacctfund = OneOf(*INVSUBACCTS, required=True)
    loanid = String(32)
    loanprincipal = Decimal()
    loaninterest = Decimal()
    inv401ksource = OneOf(*INV401KSOURCES)
    dtpayroll = DateTime()
    prioryearcontrib = Bool()


class INVSELL(INVTRAN, SECID, ORIGCURRENCY):
    units = Decimal(required=True)
    unitprice = Decimal(4, required=True)
    markdown = Decimal()
    commission = Decimal()
    taxes = Decimal()
    fees = Decimal()
    load = Decimal()
    withholding = Decimal()
    taxexempt = Bool()
    total = Decimal(required=True)
    gain = Decimal()
    subacctsec = OneOf(*INVSUBACCTS, required=True)
    subacctfund = OneOf(*INVSUBACCTS, required=True)
    loanid = String(32)
    statewithholding = Decimal()
    penalty = Decimal()
    inv401ksource = OneOf(*INV401KSOURCES)


class BUYDEBT(INVBUY):
    accrdint = Decimal()


class BUYMF(INVBUY):
    buytype = OneOf(*BUYTYPES, required=True)
    relfitid = String(255)


class BUYOPT(INVBUY):
    optbuytype = OneOf('BUYTOOPEN', 'BUYTOCLOSE', required=True)
    shperctrct = Integer(required=True)


class BUYOTHER(INVBUY):
    pass


class BUYSTOCK(INVBUY):
    buytype = OneOf(*BUYTYPES, required=True)


class CLOSUREOPT(INVTRAN, SECID):
    optaction = OneOf('EXERCISE', 'ASSIGN', 'EXPIRE')
    units = Decimal(required=True)
    shperctrct = Integer(required=True)
    subacctsec = OneOf(*INVSUBACCTS, required=True)
    relfitid = String(255)
    gain = Decimal()


class INCOME(INVTRAN, SECID, ORIGCURRENCY):
    incometype = OneOf(*INCOMETYPES, required=True)
    total = Decimal(required=True)
    subacctsec = OneOf(*INVSUBACCTS, required=True)
    subacctfund = OneOf(*INVSUBACCTS, required=True)
    taxexempt = Bool()
    withholding = Decimal()
    inv401ksource = OneOf(*INV401KSOURCES)


class INVEXPENSE(INVTRAN, SECID, ORIGCURRENCY):
    total = Decimal(required=True)
    subacctsec = OneOf(*INVSUBACCTS, required=True)
    subacctfund = OneOf(*INVSUBACCTS, required=True)
    inv401ksource = OneOf(*INV401KSOURCES)


class JRNLFUND(INVTRAN):
    subacctto = OneOf(*INVSUBACCTS, required=True)
    subacctfrom = OneOf(*INVSUBACCTS, required=True)
    total = Decimal(required=True)


class JRNLSEC(INVTRAN, SECID):
    subacctto = OneOf(*INVSUBACCTS, required=True)
    subacctfrom = OneOf(*INVSUBACCTS, required=True)
    units = Decimal(required=True)


class MARGININTEREST(INVTRAN, ORIGCURRENCY):
    total = Decimal(required=True)
    subacctfund = OneOf(*INVSUBACCTS, required=True)


class REINVEST(INVTRAN, SECID, ORIGCURRENCY):
    incometype = OneOf(*INCOMETYPES, required=True)
    total = Decimal(required=True)
    subacctsec = OneOf(*INVSUBACCTS, required=True)
    units = Decimal(required=True)
    unitprice = Decimal(4, required=True)
    commission = Decimal()
    taxes = Decimal()
    fees = Decimal()
    load = Decimal()
    taxexempt = Bool()
    inv401ksource = OneOf(*INV401KSOURCES)


class RETOFCAP(INVTRAN, SECID, ORIGCURRENCY):
    total = Decimal(required=True)
    subacctsec = OneOf(*INVSUBACCTS, required=True)
    subacctfund = OneOf(*INVSUBACCTS, required=True)
    inv401ksource = OneOf(*INV401KSOURCES)


class SELLDEBT(INVSELL):
    sellreason = OneOf('CALL', 'SELL', 'MATURITY', required=True)
    accrdint = Decimal()


class SELLMF(INVSELL):
    selltype = OneOf(*SELLTYPES, required=True)
    avgcostbasis = Decimal()
    relfitid = String(255)


class SELLOPT(INVSELL):
    optselltype = OneOf('SELLTOCLOSE', 'SELLTOOPEN', required=True)
    shperctrct = Integer(required=True)
    relfitid = String(255)
    reltype = OneOf('SPREAD', 'STRADDLE', 'NONE', 'OTHER')
    secured = OneOf('NAKED', 'COVERED')


class SELLOTHER(INVSELL):
    pass


class SELLSTOCK(INVSELL):
    selltype = OneOf(*SELLTYPES, required=True)


class SPLIT(INVTRAN, SECID):
    subacctsec = OneOf(*INVSUBACCTS, required=True)
    oldunits = Decimal(required=True)
    newunits = Decimal(required=True)
    numerator = Decimal(required=True)
    denominator = Decimal(required=True)
    fraccash = Decimal()
    subacctfund = OneOf(*INVSUBACCTS)
    inv401ksource = OneOf(*INV401KSOURCES)


class TRANSFER(INVTRAN, SECID):
    subacctsec = OneOf(*INVSUBACCTS, required=True)
    units = Decimal(required=True)
    tferaction = OneOf('IN', 'OUT', required=True)
    postype = OneOf('SHORT', 'LONG', required=True)
    avgcostbasis = Decimal()
    unitprice = Decimal()
    dtpurchase = DateTime()
    inv401ksource = OneOf(*INV401KSOURCES)


# Positions
class INVPOS(SECID, CURRENCY):
    heldinacct = OneOf(*INVSUBACCTS, required=True)
    postype = OneOf('SHORT', 'LONG', required=True)
    units = Decimal(required=True)
    unitprice = Decimal(4, required=True)
    mktval = Decimal(required=True)
    dtpriceasof = DateTime(required=True)
    memo = String(255)
    inv401ksource = OneOf(*INV401KSOURCES)


class POSDEBT(INVPOS):
    pass


class POSMF(INVPOS):
    unitsstreet = Decimal()
    unitsuser = Decimal()
    reinvdiv = Bool()
    reinvcg = Bool()


class POSOPT(INVPOS):
    secured = OneOf('NAKED', 'COVERED')


class POSOTHER(INVPOS):
    pass


class POSSTOCK(INVPOS):
    unitsstreet = Decimal()
    unitsuser = Decimal()
    reinvdiv = Bool()
