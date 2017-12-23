import time
import Queue
import logging
import binascii
import functools

import idaapi
from .shims import using_ida7api, using_pyqt5, QtCore, QtGui, QtWidgets

logger = logging.getLogger("Lighthouse.Util.IDA")

#------------------------------------------------------------------------------
# HexRays Util
#------------------------------------------------------------------------------

def map_line2citem(decompilation_text):
    """
    Map decompilation line numbers to citems.

    -----------------------------------------------------------------------

    This function allows us to build a relationship between citems in the
    ctree and specific lines in the hexrays decompilation text.

    -----------------------------------------------------------------------

    Output:

        +- line2citem:
        |    a map keyed with line numbers, holding sets of citem indexes
        |
        |      eg: { int(line_number): sets(citem_indexes), ... }
        '

    """
    line2citem = {}

    #
    # it turns out that citem indexes are actually stored inline with the
    # decompilation text output, hidden behind COLOR_ADDR tokens.
    #
    # here we pass each line of raw decompilation text to our crappy lexer,
    # extracting any COLOR_ADDR tokens as citem indexes
    #

    for line_number in xrange(decompilation_text.size()):
        line_text = decompilation_text[line_number].line
        line2citem[line_number] = lex_citem_indexes(line_text)
        #logger.debug("Line Text: %s" % binascii.hexlify(line_text))

    return line2citem

def map_line2node(cfunc, metadata, line2citem):
    """
    Map decompilation line numbers to node (basic blocks) addresses.

    -----------------------------------------------------------------------

    This function allows us to build a relationship between graph nodes
    (basic blocks) and specific lines in the hexrays decompilation text.

    -----------------------------------------------------------------------

    Output:

        +- line2node:
        |    a map keyed with line numbers, holding sets of node addresses
        |
        |      eg: { int(line_number): set(nodes), ... }
        '

    """
    line2node = {}
    treeitems = cfunc.treeitems
    function_address = cfunc.entry_ea

    #
    # prior to this function, a line2citem map was built to tell us which
    # citems reside on any given line of text in the decompilation output.
    #
    # now, we walk through this line2citem map one 'line_number' at a time in
    # an effort to resolve the set of graph nodes associated with its citems.
    #

    for line_number, citem_indexes in line2citem.iteritems():
        nodes = set()

        #
        # we are at the level of a single line (line_number). we now consume
        # its set of citems (citem_indexes) and attempt to identify the explict
        # graph nodes they claim to be sourced from (by their reported EA)
        #

        for index in citem_indexes:

            # get the code address of the given citem
            try:
                item = treeitems[index]
                address = item.ea

            # apparently this is a thing on IDA 6.95
            except IndexError as e:
                continue

            # find the graph node (eg, basic block) that generated this citem
            node = metadata.get_node(address)

            # address not mapped to a node... weird. continue to the next citem
            if not node:
                #logger.warning("Failed to map node to basic block")
                continue

            #
            # we made it this far, so we must have found a node that contains
            # this citem. save the computed node_id to the list of of known
            # nodes we have associated with this line of text
            #

            nodes.add(node.address)

        #
        # finally, save the completed list of node ids as identified for this
        # line of decompilation text to the line2node map that we are building
        #

        line2node[line_number] = nodes

    # all done, return the computed map
    return line2node

def lex_citem_indexes(line):
    """
    Lex all ctree item indexes from a given line of text.

    -----------------------------------------------------------------------

    The HexRays decompiler output contains invisible text tokens that can
    be used to attribute spans of text to the ctree items that produced them.

    This function will simply scrape and return a list of all the these
    tokens (COLOR_ADDR) which contain item indexes into the ctree.

    """
    i = 0
    indexes = []
    line_length = len(line)

    # lex COLOR_ADDR tokens from the line of text
    while i < line_length:

        # does this character mark the start of a new COLOR_* token?
        if line[i] == idaapi.COLOR_ON:

            # yes, so move past the COLOR_ON byte
            i += 1

            # is this sequence for a COLOR_ADDR?
            if ord(line[i]) == idaapi.COLOR_ADDR:

                # yes, so move past the COLOR_ADDR byte
                i += 1

                #
                # A COLOR_ADDR token is followed by either 8, or 16 characters
                # (a hex encoded number) that represents an address/pointer.
                # in this context, it is actually the index number of a citem
                #

                citem_index = int(line[i:i+idaapi.COLOR_ADDR_SIZE], 16)
                i += idaapi.COLOR_ADDR_SIZE

                # save the extracted citem index
                indexes.append(citem_index)

                # skip to the next iteration as i has moved
                continue

        # nothing we care about happened, keep lexing forward
        i += 1

    # return all the citem indexes extracted from this line of text
    return indexes

#------------------------------------------------------------------------------
# Misc
#------------------------------------------------------------------------------

def touch_window(target):
    """
    Touch a window/widget/form to ensure it gets drawn by IDA.

    XXX/HACK:

      We need to ensure that widget we will analyze actually gets drawn
      so that there are colors for us to steal.

      To do this, we switch to it, and switch back. I tried a few different
      ways to trigger this from Qt, but could only trigger the full
      painting by going through the IDA routines.

    """

    # get the currently active widget/form title (the form itself seems transient...)
    if using_ida7api:
        twidget = idaapi.get_current_widget()
        title = idaapi.get_widget_title(twidget)
    else:
        form = idaapi.get_current_tform()
        title = idaapi.get_tform_title(form)

    # touch/draw the widget by playing musical chairs
    if using_ida7api:

        # touch the target window by switching to it
        idaapi.activate_widget(target, True)
        flush_ida_sync_requests()

        # locate our previous selection
        previous_twidget = idaapi.find_widget(title)

        # return us to our previous selection
        idaapi.activate_widget(previous_twidget, True)
        flush_ida_sync_requests()

    else:

        # touch the target window by switching to it
        idaapi.switchto_tform(target, True)
        flush_ida_sync_requests()

        # locate our previous selection
        previous_form = idaapi.find_tform(title)

        # lookup our original form and switch back to it
        idaapi.switchto_tform(previous_form, True)
        flush_ida_sync_requests()

def get_ida_bg_color():
    """
    Get the background color of an IDA disassembly view.

    -----------------------------------------------------------------------

    The necessity of this function is pretty silly. I would like lighthouse
    to be color-aware of the user's IDA theme such that it selects reasonable
    colors that maintain readability.

    Since there is no supported way to probe the palette & colors in use by
    IDA, we must get creative. This function attempts to locate an IDA
    disassembly view, and take a screenshot of said widget. It will then
    attempt to extract the color of a single background pixel (hopefully).

    PS: please expose the get_graph_color(...) palette accessor, Ilfak ;_;
    """
    if using_ida7api:
        return get_ida_bg_color_ida7()
    else:
        return get_ida_bg_color_ida6()

def get_ida_bg_color_ida7():
    """
    Get the background color of an IDA disassembly view. (IDA 7+)
    """
    names  = ["Enums", "Structures"]
    names += ["Hex View-%u" % i for i in range(5)]
    names += ["IDA View-%c" % chr(ord('A') + i) for i in range(5)]

    # find a form (eg, IDA view) to analyze colors from
    for window_name in names:
        twidget = idaapi.find_widget(window_name)
        if twidget:
            break
    else:
        raise RuntimeError("Failed to find donor view")

    # touch the target form so we know it is populated
    touch_window(twidget)

    # locate the Qt Widget for a form and take 1px image slice of it
    import sip
    widget = sip.wrapinstance(long(twidget), QtWidgets.QWidget)
    pixmap = widget.grab(QtCore.QRect(0, 10, widget.width(), 1))

    # convert the raw pixmap into an image (easier to interface with)
    image = QtGui.QImage(pixmap.toImage())

    # return the predicted background color
    return QtGui.QColor(predict_bg_color(image))

def get_ida_bg_color_ida6():
    """
    Get the background color of an IDA disassembly view. (IDA 6.x)
    """
    names  = ["Enums", "Structures"]
    names += ["Hex View-%u" % i for i in range(5)]
    names += ["IDA View-%c" % chr(ord('A') + i) for i in range(5)]

    # find a form (eg, IDA view) to analyze colors from
    for window_name in names:
        form = idaapi.find_tform(window_name)
        if form:
            break
    else:
        raise RuntimeError("Failed to find donor View")

    # touch the target form so we know it is populated
    touch_window(form)

    # locate the Qt Widget for a form and take 1px image slice of it
    if using_pyqt5:
        widget = idaapi.PluginForm.FormToPyQtWidget(form)
        pixmap = widget.grab(QtCore.QRect(0, 10, widget.width(), 1))
    else:
        widget = idaapi.PluginForm.FormToPySideWidget(form)
        region = QtCore.QRect(0, 10, widget.width(), 1)
        pixmap = QtGui.QPixmap.grabWidget(widget, region)

    # convert the raw pixmap into an image (easier to interface with)
    image = QtGui.QImage(pixmap.toImage())

    # return the predicted background color
    return QtGui.QColor(predict_bg_color(image))

def predict_bg_color(image):
    """
    Predict the background color of an IDA View from a given image slice.

    We hypothesize that the 'background color' of a given image slice of an
    IDA form will be the color that appears in the longest 'streaks' or
    continuous sequences. This will probably be true 99% of the time.

    This function takes an image, and analyzes its first row of pixels. It
    will return the color that it believes to be the 'background color' based
    on its sequence length.
    """
    assert image.width() and image.height()

    # the details for the longest known color streak will be saved in these
    longest = 1
    speculative_bg = image.pixel(0, 0)

    # this will be the computed length of the current color streak
    sequence = 1

    # find the longest streak of color in a single pixel slice
    for x in xrange(1, image.width()):

        # the color of this pixel matches the last pixel, extend the streak count
        if image.pixel(x, 0) == image.pixel(x-1,0):
            sequence += 1

            #
            # this catches the case where the longest color streak is in fact
            # the last one. this ensures the streak color will get saved.
            #

            if x != image.width():
                continue

        # color change, determine if this was the longest continuous color streak
        if sequence > longest:

            # save the last pixel as the longest seqeuence / most likely BG color
            longest = sequence
            speculative_bg = image.pixel(x-1, 0)

            # reset the sequence counter
            sequence = 1

    # return the color we speculate to be the background color
    return speculative_bg

#------------------------------------------------------------------------------
# IDA execute_sync decorators
#------------------------------------------------------------------------------
# from: Will Ballenthin
# http://www.williballenthin.com/blog/2015/09/04/idapython-synchronization-decorator
#

def idafast(f):
    """
    Decorator for marking a function as fast / UI event
    """
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        ff = functools.partial(f, *args, **kwargs)
        if idaapi.is_main_thread():
            return ff()
        else:
            return idaapi.execute_sync(ff, idaapi.MFF_FAST)
    return wrapper

def idawrite_async(f):
    """
    Decorator for marking a function as completely async.
    """
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        ff = functools.partial(f, *args, **kwargs)
        return idaapi.execute_sync(ff, idaapi.MFF_NOWAIT | idaapi.MFF_WRITE)
    return wrapper

def idawrite(f):
    """
    Decorator for marking a function as modifying the IDB.
    """
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        ff = functools.partial(f, *args, **kwargs)
        if idaapi.is_main_thread():
            return ff()
        else:
            return idaapi.execute_sync(ff, idaapi.MFF_WRITE)
    return wrapper

def idaread(f):
    """
    Decorator for marking a function as reading from the IDB.

    MFF_READ constant via: http://www.openrce.org/forums/posts/1827
    """
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        ff = functools.partial(f, *args, **kwargs)
        if idaapi.is_main_thread():
            return ff()
        else:
            return idaapi.execute_sync(ff, idaapi.MFF_READ)
    return wrapper

def mainthread(f):
    """
    A debug decorator to assert main thread execution.
    """
    def wrapper(*args, **kwargs):
        assert idaapi.is_main_thread()
        return f(*args, **kwargs)
    return wrapper

def execute_sync(sync_flags=idaapi.MFF_FAST):
    """
    Synchronization decorator capable of providing return values.

    From https://github.com/vrtadmin/FIRST-plugin-ida
    """
    def real_decorator(function):
        @functools.wraps(function)
        def wrapper(*args, **kwargs):
            output = [None]

            #
            # this inline function definition is technically what will execute
            # in the context of the main thread. we use this thunk to capture
            # any output the function may want to return to the user.
            #

            def thunk():
                output[0] = function(*args, **kwargs)
                return 1

            # already in the target (main) thread, execute thunk now
            if idaapi.is_main_thread():
                thunk()

            # send the synchronization request to IDA
            else:
                idaapi.execute_sync(thunk, sync_flags)

            # return the output of the synchronized function
            return output[0]
        return wrapper
    return real_decorator

#------------------------------------------------------------------------------
# IDA Async Magic
#------------------------------------------------------------------------------

def await_future(future):
    """
    This is effectively a technique I use to get around completely blocking
    IDA's mainthread while waiting for a threaded result that may need to make
    use of the execute_sync operators.

    Waiting for a 'future' thread result to come through via this function
    lets other execute_sync actions to slip through (at least Read, Fast).
    """
    interval = 0.02    # the interval which we wait for a response

    # run until the the future arrives
    while True:

        # block for a brief period to see if the future completes
        try:
            return future.get(timeout=interval)

        #
        # the future timed out, so perhaps it is blocked on a request
        # to the mainthread. flush the requests now and try again
        #

        except Queue.Empty as e:
            pass

        logger.debug("Awaiting future...")

        #
        # if we are executing (well, blocking) as the main thread, we need
        # to flush the event loop so IDA does not hang
        #

        if idaapi.is_main_thread():
            flush_ida_sync_requests()

def await_lock(lock):
    """
    Attempt to acquire a lock without blocking the IDA mainthread.

    See await_future() for more details.
    """

    elapsed  = 0       # total time elapsed waiting for the lock
    interval = 0.02    # the interval (in seconds) between acquire attempts
    timeout  = 60.0    # the total time allotted to acquiring the lock
    end_time = time.time() + timeout

    # wait until the the lock is available
    while time.time() < end_time:

        #
        # attempt to acquire the given lock without blocking (via 'False').
        # if we succesfully aquire the lock, then we can return (success)
        #

        if lock.acquire(False):
            logger.debug("Acquired lock!")
            return

        #
        # the lock is not available yet. we need to sleep so we don't choke
        # the cpu, and try to acquire the lock again next time through...
        #

        logger.debug("Awaiting lock...")
        time.sleep(interval)

        #
        # if we are executing (well, blocking) as the main thread, we need
        # to flush the event loop so IDA does not hang
        #

        if idaapi.is_main_thread():
            flush_ida_sync_requests()

    #
    # we spent 60 seconds trying to acquire the lock, but never got it...
    # to avoid hanging IDA indefinitely (or worse), we abort via signal
    #

    raise RuntimeError("Failed to acquire lock after %f seconds!" % timeout)

@mainthread
def flush_ida_sync_requests():
    """
    Flush all execute_sync requests.
    """

    # this will trigger/flush the IDA UI loop
    qta = QtCore.QCoreApplication.instance()
    qta.processEvents()

#------------------------------------------------------------------------------
# IDA Util
#------------------------------------------------------------------------------

# taken from https://github.com/gaasedelen/prefix
PREFIX_DEFAULT = "MyPrefix"
PREFIX_SEPARATOR = '%'

def prefix_function(function_address, prefix):
    """
    Prefix a function name with the given string.
    """
    original_name = get_function_name(function_address)
    new_name = str(prefix) + PREFIX_SEPARATOR + str(original_name)

    # rename the function with the newly prefixed name
    idaapi.set_name(function_address, new_name, idaapi.SN_NOWARN)

def prefix_functions(function_addresses, prefix):
    """
    Prefix a list of functions with the given string.
    """
    for function_address in function_addresses:
        prefix_function(function_address, prefix)

def clear_prefix(function_address):
    """
    Clear the prefix from a given function.
    """
    original_name = get_function_name(function_address)

    #
    # locate the last (rfind) prefix separator in the function name as
    # we will want to keep everything that comes after it
    #

    i = original_name.rfind(PREFIX_SEPARATOR)

    # if there is no prefix (separator), there is nothing to trim
    if i == -1:
        return

    # trim the prefix off the original function name and discard it
    new_name = original_name[i+1:]

    # rename the function with the prefix stripped
    idaapi.set_name(function_address, new_name, idaapi.SN_NOWARN)

def clear_prefixes(function_addresses):
    """
    Clear the prefix from a list of given functions.
    """
    for function_address in function_addresses:
        clear_prefix(function_address)

def get_function_name(function_address):
    """
    Get a function's true name.
    """

    # get the original function name from the database
    if using_ida7api:
        original_name = idaapi.get_name(function_address)
    else:
        original_name = idaapi.get_true_name(idaapi.BADADDR, function_address)

    # sanity check
    if original_name == None:
        raise ValueError("Invalid function address")

    # return the function name
    return original_name

#------------------------------------------------------------------------------
# Interactive
#------------------------------------------------------------------------------

@mainthread
def prompt_string(label, title, default=""):
    """
    Prompt the user with a dialog to enter a string.

    This does not block the IDA main thread (unlike idaapi.askstr)
    """
    dlg = QtWidgets.QInputDialog(None)
    dlg.setWindowFlags(dlg.windowFlags() & ~QtCore.Qt.WindowContextHelpButtonHint)
    dlg.setInputMode(QtWidgets.QInputDialog.TextInput)
    dlg.setLabelText(label)
    dlg.setWindowTitle(title)
    dlg.setTextValue(default)
    dlg.resize(
        dlg.fontMetrics().averageCharWidth()*80,
        dlg.fontMetrics().averageCharWidth()*10
    )
    ok = dlg.exec_()
    text = str(dlg.textValue())
    return (ok, text)

@mainthread
def gui_rename_function(function_address):
    """
    Interactive rename of a function in the IDB.
    """
    original_name = get_function_name(function_address)

    # prompt the user for a new function name
    ok, new_name = prompt_string(
        "Please enter function name",
        "Rename Function",
        original_name
       )

    #
    # if the user clicked cancel, or the name they entered
    # is identical to the original, there's nothing to do
    #

    if not (ok or new_name != original_name):
        return

    # rename the function
    idaapi.set_name(function_address, new_name, idaapi.SN_NOCHECK)

@mainthread
def gui_prefix_functions(function_addresses):
    """
    Interactive prefixing of functions in the IDB.
    """

    # prompt the user for a new function name
    ok, prefix = prompt_string(
        "Please enter a function prefix",
        "Prefix Function(s)",
        PREFIX_DEFAULT
       )

    # bail if the user clicked cancel or failed to enter a prefix
    if not (ok and prefix):
        return

    # prefix the given functions with the user specified prefix
    prefix_functions(function_addresses, prefix)
