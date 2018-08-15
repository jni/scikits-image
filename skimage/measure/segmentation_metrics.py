import numpy as np
import multiprocessing
from .simple_metrics import _assert_compatible
import scipy.sparse as sparse
from scipy.ndimage.measurements import label

__all__ = [ 'compare_adapted_rand_error',
            'compare_rand_by_threshold',
            'compare_raw_edit_distance',
            'compare_split_vi',
            'compare_vi',
          ]

def compare_raw_edit_distance(im_true, im_test, size_threshold=1000):
    """Compute the edit distance between two segmentations.

    Parameters
    ----------
    im_true : ndarray
        Ground-truth image.
    im_test : ndarray
        Test image.
    size_threshold : int or float, optional
        Ignore splits or merges smaller than this number of voxels.

    Returns
    -------
    (false_merges, false_splits) : float
        The number of splits and merges required to convert aseg to gt.
    """
    _assert_compatible(im_true, im_test)

    im_test = relabel_from_one(im_test)[0]
    im_true = relabel_from_one(im_true)[0]
    r = contingency_table(im_true, im_test)
    r.data[r.data <= size_threshold] = 0
    # make each segment overlap count for 1, since it will be one
    # operation to fix (split or merge)
    r.data[r.data.nonzero()] /= r.data[r.data.nonzero()]
    false_splits = (r.sum(axis=0)-1)[1:].sum()
    false_merges = (r.sum(axis=1)-1)[1:].sum()
    return (false_merges, false_splits)

def compare_adapted_rand_error(im_true, im_test):
    """Compute Adapted Rand error as defined by the SNEMI3D contest [1]

    Formula is given as 1 - the maximal F-score of the Rand index
    (excluding the zero component of the original labels). Adapted
    from the SNEMI3D MATLAB script, hence the strange style.

    Parameters
    ----------
    im_true : ndarray
        Ground-truth image.
    im_test : ndarray
        Test image.

    Returns
    -------
    are : float
        The adapted Rand error; equal to $1 - \frac{2pr}{p + r}$,
        where $p$ and $r$ are the precision and recall described below.
    prec : float
        The adapted Rand precision.
    rec : float
        The adapted Rand recall.

    References
    ----------
    [1]: http://brainiac2.mit.edu/SNEMI3D/evaluation
    """
    _assert_compatible(im_true, im_test)

    # segA is query, segB is truth
    segA = im_test
    segB = im_true

    n = segA.size

    # This is the contingency table obtained from segA and segB, we obtain
    # the marginal probabilities from the table.
    p_ij = contingency_table(segB, segA)

    # Sum of the joint distribution squared
    sum_p_ij = p_ij.data @ p_ij.data

    # These are the axix-wise sums (np.sumaxis)
    a_i = p_ij.sum(axis=0).A.ravel()
    b_i = p_ij.sum(axis=1).A.ravel()

    # Sum of the segment labeled 'A'
    sum_a = a_i @ a_i
    # Sum of the segment labeled 'B'
    sum_b = b_i @ b_i

    # This is the new code, wherein 'n' is subtacted from the numerator
    # and the denominator.

    precision = (sum_p_ij - n)/ (sum_a - n)
    recall = (sum_p_ij - n)/ (sum_b - n)

    fscore = 2. * precision * recall / (precision + recall)
    are = 1. - fscore

    return (are, precision, recall)

def compare_rand_by_threshold(im_true, im_test, npoints=None):
    """Compute Rand and Adjusted Rand indices for each threshold of a UCM

    Parameters
    ----------
    im_true : ndarray
        Ground-truth image.
    im_test : ndarray
        Test image.
    npoints : int, optional
        If provided, only compute values at npoints thresholds, rather than
        all thresholds. Useful when ucm has an extremely large number of
        unique values.

    Returns
    -------
    ris : ndarray of float, shape (3, len(np.unique(ucm))) or (3, npoints)
        The rand indices of the segmentation induced by thresholding and
        labeling `ucm` at different values. The 3 rows of `ris` are the values
        used for thresholding, the corresponding Rand Index at that threshold,
        and the corresponding Adjusted Rand Index at that threshold.
    """
    _assert_compatible(im_true, im_test)

    ts = np.unique(im_test)[1:]
    if npoints is None:
        npoints = len(ts)
    if len(ts) > 2 * npoints:
        ts = ts[np.arange(1, len(ts), len(ts) / npoints)]
    result = np.zeros((2, len(ts)))
    for i, t in enumerate(ts):
        seg = label(im_test < t)[0]
        result[0, i] = rand_index(seg, im_true)
        result[1, i] = adj_rand_index(seg, im_true)
    return np.concatenate((ts[np.newaxis, :], result), axis=0)

def compare_vi(im1, im2, weights=np.ones(2)):
    """Return the variation of information metric. [1]

    VI(X, Y) = H(X | Y) + H(Y | X), where H(.|.) denotes the conditional
    entropy.

    Parameters
    ----------
    im1, im2 : ndarray
        Image.  Any dimensionality.
    weights : ndarray of float, shape (2,), optional
        The weights of the conditional entropies of `im1` and `im2`. Equal weights
        are the default.

    Returns
    -------
    v : float
        The variation of information between `im1` and `im2`.

    References
    ----------
    [1] Meila, M. (2007). Comparing clusterings - an information based
    distance. Journal of Multivariate Analysis 98, 873-895.
    """
    return np.dot(weights, compare_split_vi(im1, im2))

def compare_split_vi(im1, im2):
    """Return the symmetric conditional entropies associated with the VI.

    The variation of information is defined as VI(X,Y) = H(X|Y) + H(Y|X).
    If Y is the ground-truth segmentation, then H(Y|X) can be interpreted
    as the amount of under-segmentation of Y and H(X|Y) is then the amount
    of over-segmentation.  In other words, a perfect over-segmentation
    will have H(Y|X)=0 and a perfect under-segmentation will have H(X|Y)=0.

    Parameters
    ----------
    im1, im2 : ndarray
        Image.  Any dimensionality.

    Returns
    -------
    sv : ndarray of float, shape (2,)
        The conditional entropies of im2|im1 and im1|im2.

    See Also
    --------
    vi
    """
    _, _, _ , hxgy, hygx, _, _ = vi_tables(im1, im2)
    # false merges, false splits
    return np.array([hygx.sum(), hxgy.sum()])

def contingency_table(im_true, im_test):
    """Return the contingency table for all regions in matched segmentations.

    Parameters
    ----------
    im_true : ndarray
        Ground-truth image.
    im_test : ndarray
        Test image.

    Returns
    -------
    cont : scipy.sparse.csr_matrix
        A contingency table. `cont[i, j]` will equal the number of voxels
        labeled `i` in `im_test` and `j` in `im_true`.
    """
    im_test_r = im_test.ravel()
    im_true_r = im_true.ravel()
    ignored = np.zeros(im_test_r.shape, np.bool)
    data = np.ones(im_true_r.shape)
    ignored[im_test_r == 0] = True
    ignored[im_true_r == 0] = True
    data[ignored] = 0
    cont = sparse.coo_matrix((data, (im_test_r, im_true_r))).tocsr()
    return cont
 
def relabel_from_one(label_field):
    """Convert labels in an arbitrary label field to {1, ... number_of_labels}.

    This function also returns the forward map (mapping the original labels to
    the reduced labels) and the inverse map (mapping the reduced labels back
    to the original ones).

    Parameters
    ----------
    label_field : ndarray (integer type)

    Returns
    -------
    relabeled : numpy array of same shape as ar
    forward_map : 1d numpy array of length np.unique(ar) + 1
    inverse_map : 1d numpy array of length len(np.unique(ar))
        The length is len(np.unique(ar)) + 1 if 0 is not in np.unique(ar)

    Examples
    --------
    >>> import numpy as np
    >>> label_field = np.array([1, 1, 5, 5, 8, 99, 42])
    >>> relab, fw, inv = relabel_from_one(label_field)
    >>> relab
    array([1, 1, 2, 2, 3, 5, 4])
    >>> fw
    array([0, 1, 0, 0, 0, 2, 0, 0, 3, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
           0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 4, 0, 0, 0,
           0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
           0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
           0, 0, 0, 0, 0, 0, 0, 5])
    >>> inv
    array([ 0,  1,  5,  8, 42, 99])
    >>> (fw[label_field] == relab).all()
    True
    >>> (inv[relab] == label_field).all()
    True
    """
    labels = np.unique(label_field)
    labels0 = labels[labels != 0]
    m = labels.max()
    if m == len(labels0): # nothing to do, already 1...n labels
        return label_field, labels, labels
    forward_map = np.zeros(m+1, int)
    forward_map[labels0] = np.arange(1, len(labels0) + 1)
    if not (labels == 0).any():
        labels = np.concatenate(([0], labels))
    inverse_map = labels
    return forward_map[label_field], forward_map, inverse_map

def xlogx(x):
    """Compute x * log_2(x).

    We define 0 * log_2(0) = 0

    Parameters
    ----------
    x : ndarray or scipy.sparse.csc_matrix or csr_matrix
        The input array.

    Returns
    -------
    y : same type as x
        Result of x * log_2(x).
    """
    y = x.copy()
    if isinstance(y, sparse.csc_matrix) or isinstance(y, sparse.csr_matrix):
        z = y.data
    else:
        z = np.asarray(y)  # ensure np.matrix converted to np.array
    nz = z.nonzero()
    z[nz] *= np.log2(z[nz])
    return y

def divide_rows(matrix, column):
    """Divide each row of `matrix` by the corresponding element in `column`.

    The result is as follows: out[i, j] = matrix[i, j] / column[i]

    Parameters
    ----------
    matrix : ndarray, scipy.sparse.csc_matrix or csr_matrix, shape (M, N)
        The input matrix.
    column : a 1D ndarray, shape (M,)
        The column dividing `matrix`.

    Returns
    -------
    out : same type as `matrix`
        The result of the row-wise division.
    """
    out = matrix.copy()
    if type(out) in [sparse.csc_matrix, sparse.csr_matrix]:
        if type(out) == sparse.csr_matrix:
            convert_to_csr = True
            out = out.tocsc()
        else:
            convert_to_csr = False
        column_repeated = np.take(column, out.indices)
        nz = out.data.nonzero()
        out.data[nz] /= column_repeated[nz]
        if convert_to_csr:
            out = out.tocsr()
    else:
        out /= column[:, np.newaxis]
    return out

def divide_columns(matrix, row):
    """Divide each column of `matrix` by the corresponding element in `row`.

    The result is as follows: out[i, j] = matrix[i, j] / row[j]

    Parameters
    ----------
    matrix : ndarray, scipy.sparse.csc_matrix or csr_matrix, shape (M, N)
        The input matrix.
    column : a 1D ndarray, shape (N,)
        The row dividing `matrix`.

    Returns
    -------
    out : same type as `matrix`
        The result of the row-wise division.
    """
    out = matrix.copy()
    if type(out) in [sparse.csc_matrix, sparse.csr_matrix]:
        if type(out) == sparse.csc_matrix:
            convert_to_csc = True
            out = out.tocsr()
        else:
            convert_to_csc = False
        row_repeated = np.take(row, out.indices)
        nz = out.data.nonzero()
        out.data[nz] /= row_repeated[nz]
        if convert_to_csc:
            out = out.tocsc()
    else:
        out /= row[np.newaxis, :]
    return out

def vi_tables(im1, im2):
    """Return probability tables used for calculating VI.

    Parameters
    ----------
    im1, im2 : ndarray
        Image.  Any dimensionality.

    Returns
    -------
    pxy : sparse.csc_matrix of float
        The normalized contingency table.
    px, py, hxgy, hygx, lpygx, lpxgy : ndarray of float
        The proportions of each label in `im1` and `im2` (`px`, `py`), the
        per-segment conditional entropies of `im1` given `im2` and vice-versa, the
        per-segment conditional probability p log p.
    """
    cont = im1
    total = float(cont.sum())
    # normalize, since it is an identity op if already done
    pxy = cont / total

    # Calculate probabilities
    px = np.array(pxy.sum(axis=1)).ravel()
    py = np.array(pxy.sum(axis=0)).ravel()
    # Remove zero rows/cols
    nzx = px.nonzero()[0]
    nzy = py.nonzero()[0]
    nzpx = px[nzx]
    nzpy = py[nzy]
    nzpxy = pxy[nzx, :][:, nzy]

    # Calculate log conditional probabilities and entropies
    lpygx = np.zeros(np.shape(px))
    lpygx[nzx] = xlogx(divide_rows(nzpxy, nzpx)).sum(axis=1).ravel()
                        # \sum_x{p_{y|x} \log{p_{y|x}}}
    hygx = -(px*lpygx) # \sum_x{p_x H(Y|X=x)} = H(Y|X)

    lpxgy = np.zeros(np.shape(py))
    lpxgy[nzy] = xlogx(divide_columns(nzpxy, nzpy)).sum(axis=0).ravel()
    hxgy = -(py*lpxgy)

    return [pxy] + list(map(np.asarray, [px, py, hxgy, hygx, lpygx, lpxgy]))

def rand_values(cont_table):
    """Calculate values for Rand Index and related values, e.g. Adjusted Rand.

    Parameters
    ----------
    cont_table : scipy.sparse.csc_matrix
        A contingency table of the two segmentations.

    Returns
    -------
    a, b, c, d : float
        The values necessary for computing Rand Index and related values. [1, 2]
    a : float
        Refers to the number of pairs of elements in the input image that are
        both the same in seg1 and in seg2,
    b : float
        Refers to the number of pairs of elements in the input image that are
        different in both seg1 and in seg2.
    c : float
        Refers to the number of pairs of elements in the input image that are
        the same in seg1 but different in seg2.
    d : float
        Refers to the number of pairs of elements in the input image that are
        different in seg1 but the same in seg2.


    References
    ----------
    [1] Rand, W. M. (1971). Objective criteria for the evaluation of
    clustering methods. J Am Stat Assoc.
    [2] http://en.wikipedia.org/wiki/Rand_index#Definition on 2013-05-16.
    """
    n = cont_table.sum()
    sum1 = (cont_table.multiply(cont_table)).sum()
    sum2 = (np.asarray(cont_table.sum(axis=1)) ** 2).sum()
    sum3 = (np.asarray(cont_table.sum(axis=0)) ** 2).sum()
    a = (sum1 - n)/2.0;
    b = (sum2 - sum1)/2
    c = (sum3 - sum1)/2
    d = (sum1 + n**2 - sum2 - sum3)/2
    return a, b, c, d

def rand_index(im1, im2):
    """Return the unadjusted Rand index. [1]

    Parameters
    ----------
    im1, im2 : ndarray
        Image.  Any dimensionality.

    Returns
    -------
    ri : float
        The Rand index of `im1` and `im2`.

    References
    ----------
    [1] WM Rand. (1971) Objective criteria for the evaluation of
    clustering methods. J Am Stat Assoc. 66: 846–850
    """
    cont = contingency_table(im1, im2)
    a, b, c, d = rand_values(cont)
    return (a+d)/(a+b+c+d)

def adj_rand_index(im1, im2):
    """Return the adjusted Rand index.

    The Adjusted Rand Index (ARI) is the deviation of the Rand Index from the
    expected value if the marginal distributions of the contingency table were
    independent. Its value ranges from 1 (perfectly correlated marginals) to
    -1 (perfectly anti-correlated).

    Parameters
    ----------
    im1, im2 : ndarray
        im1 and im2 are provided as equal-shaped ndarray label fields
        (int type).

    Returns
    -------
    ari : float
        The adjusted Rand index of `im1` and `im2`.
    """
    cont = contingency_table(im1, im2)
    a, b, c, d = rand_values(cont)
    nk = a+b+c+d
    return (nk*(a+d) - ((a+b)*(a+c) + (c+d)*(b+d)))/(
        nk**2 - ((a+b)*(a+c) + (c+d)*(b+d)))
